"""连接器归属与依赖关系的真实 HTTP 集成验证。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from yuxi.modules.extensions.mcp.models import MCPServer

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]

GLOBAL_SCOPE = {"access_level": "global", "department_ids": [], "user_uids": []}


def _share(scope: dict) -> dict:
    """构造 v2 共享配置。"""
    return {"version": 2, "read_scope": scope, "manage_scope": None}


async def _create_connector(test_client, headers: dict[str, str], slug: str) -> str:
    """通过真实管理接口创建一个已启用的远程连接器。"""
    response = await test_client.post(
        "/api/system/mcp-servers",
        headers=headers,
        json={
            "slug": slug,
            "name": f"IT {slug}",
            "transport": "streamable_http",
            "url": "https://example.com/mcp",
            "timeout": 5,
            "sse_read_timeout": 5,
        },
    )
    assert response.status_code == 200, response.text
    return slug


async def _install_skill(
    test_client,
    headers: dict[str, str],
    slug: str,
    mcp_slugs: list[str],
    *,
    created_mcp_slugs: list[str] | None = None,
) -> str:
    """上传最小 Skill 包并确认安装，返回最终落库的 slug。"""
    content = (
        "---\n"
        f"name: IT {slug}\n"
        f"slug: {slug}\n"
        "description: pytest connector ownership fixture\n"
        f"mcp_dependencies: [{', '.join(mcp_slugs)}]\n"
        "---\n"
        f"# {slug}\n"
    )
    prepared = await test_client.post(
        "/api/skills/import/prepare",
        headers=headers,
        files={"file": ("SKILL.md", content.encode(), "text/markdown")},
    )
    assert prepared.status_code == 200, prepared.text
    draft_id = prepared.json()["data"]["draft_id"]
    confirmed = await test_client.post(
        f"/api/skills/install-drafts/{draft_id}/confirm",
        headers=headers,
        json={
            "slugs": [slug],
            "share_config": None,
            "created_mcp_slugs": created_mcp_slugs or [],
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    result = confirmed.json()["data"][0]
    assert result["success"] is True, result
    return result["slug"]


async def _connector_state(postgres_sessions, slug: str) -> dict | None:
    """回读连接器归属、启停与共享范围。"""
    async with postgres_sessions() as db:
        row = (await db.execute(select(MCPServer).where(MCPServer.slug == slug))).scalar_one_or_none()
        if row is None:
            return None
        return {"origin_skill": row.origin_skill, "enabled": int(row.enabled), "share_config": row.share_config}


async def _delete_skill(test_client, headers: dict[str, str], slug: str) -> None:
    """删除共享 Skill，容忍已被用例删除。"""
    response = await test_client.delete(f"/api/system/skills/{slug}", headers=headers)
    assert response.status_code in {200, 404}, response.text


async def _delete_connector(test_client, headers: dict[str, str], slug: str) -> None:
    """删除连接器，容忍已被用例删除。"""
    response = await test_client.delete(f"/api/system/mcp-servers/{slug}", headers=headers)
    assert response.status_code in {200, 404}, response.text


async def test_skill_share_config_syncs_only_owned_connectors(test_client, admin_headers, postgres_sessions):
    """Skill 共享范围只同步到 origin_skill 匹配的连接器。"""
    base = f"it-{uuid.uuid4().hex[:10]}"
    owned = f"{base}-owned"
    manual = f"{base}-manual"
    skill_slug = f"{base}-skill"
    installed = False
    try:
        await _create_connector(test_client, admin_headers, owned)
        await _create_connector(test_client, admin_headers, manual)
        profile = await test_client.get("/api/auth/me", headers=admin_headers)
        assert profile.status_code == 200, profile.text
        uid = profile.json()["uid"]

        await _install_skill(test_client, admin_headers, skill_slug, [owned, manual], created_mcp_slugs=[owned])
        installed = True
        owned_before = await _connector_state(postgres_sessions, owned)
        manual_before = await _connector_state(postgres_sessions, manual)
        assert owned_before["origin_skill"] == skill_slug
        assert owned_before["share_config"] == _share({"access_level": "user", "department_ids": [], "user_uids": [uid]})
        assert manual_before["origin_skill"] is None
        assert manual_before["share_config"] == _share(GLOBAL_SCOPE)

        departments = await test_client.get("/api/departments", headers=admin_headers)
        assert departments.status_code == 200, departments.text
        assert departments.json(), "集成环境需要至少一个部门"
        department_scope = {
            "access_level": "department",
            "department_ids": [departments.json()[0]["id"]],
            "user_uids": [],
        }
        response = await test_client.put(
            f"/api/system/skills/{skill_slug}/share-config",
            headers=admin_headers,
            json={"share_config": _share(department_scope)},
        )
        assert response.status_code == 200, response.text

        assert (await _connector_state(postgres_sessions, owned))["share_config"] == _share(department_scope)
        assert (await _connector_state(postgres_sessions, manual))["share_config"] == manual_before["share_config"]
    finally:
        if installed:
            await _delete_skill(test_client, admin_headers, skill_slug)
        await _delete_connector(test_client, admin_headers, owned)
        await _delete_connector(test_client, admin_headers, manual)


async def test_delete_connector_reports_dependents_until_released(test_client, admin_headers, postgres_sessions):
    """删除仍被 Skill 引用的连接器返回 409 与依赖明细，解除引用后可删除。"""
    base = f"it-{uuid.uuid4().hex[:10]}"
    connector = f"{base}-mcp"
    skill_slug = f"{base}-skill"
    installed = False
    try:
        await _create_connector(test_client, admin_headers, connector)
        await _install_skill(test_client, admin_headers, skill_slug, [connector])
        installed = True

        response = await test_client.delete(f"/api/system/mcp-servers/{connector}", headers=admin_headers)
        assert response.status_code == 409, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "mcp_server_in_use"
        assert [item["slug"] for item in detail["dependents"]["skills"]] == [skill_slug]
        assert detail["dependents"]["agents"] == []
        assert await _connector_state(postgres_sessions, connector) is not None

        await _delete_skill(test_client, admin_headers, skill_slug)
        installed = False
        response = await test_client.delete(f"/api/system/mcp-servers/{connector}", headers=admin_headers)
        assert response.status_code == 200, response.text
        assert response.json()["success"] is True
        assert await _connector_state(postgres_sessions, connector) is None
    finally:
        if installed:
            await _delete_skill(test_client, admin_headers, skill_slug)
        await _delete_connector(test_client, admin_headers, connector)


async def test_dependents_endpoint_returns_skills_for_admin_and_rejects_user(
    test_client, admin_headers, standard_user, postgres_sessions
):
    """依赖列表仅管理员可读，普通用户被真实权限依赖拒绝。"""
    base = f"it-{uuid.uuid4().hex[:10]}"
    connector = f"{base}-mcp"
    skill_slug = f"{base}-skill"
    installed = False
    try:
        await _create_connector(test_client, admin_headers, connector)
        await _install_skill(test_client, admin_headers, skill_slug, [connector])
        installed = True

        response = await test_client.get(f"/api/system/mcp-servers/{connector}/dependents", headers=admin_headers)
        assert response.status_code == 200, response.text
        assert [item["slug"] for item in response.json()["data"]["skills"]] == [skill_slug]

        response = await test_client.get(
            f"/api/system/mcp-servers/{connector}/dependents", headers=standard_user["headers"]
        )
        assert response.status_code == 403, response.text
    finally:
        if installed:
            await _delete_skill(test_client, admin_headers, skill_slug)
        await _delete_connector(test_client, admin_headers, connector)


async def test_install_confirm_persists_origin_skill_only_for_claimed_connectors(
    test_client, admin_headers, postgres_sessions
):
    """确认安装时回传的新建连接器标记落库，未回传时保持公有。"""
    base = f"it-{uuid.uuid4().hex[:10]}"
    claimed = f"{base}-claimed"
    unclaimed = f"{base}-unclaimed"
    claimed_skill = f"{base}-claimed-skill"
    unclaimed_skill = f"{base}-unclaimed-skill"
    installed: list[str] = []
    try:
        await _create_connector(test_client, admin_headers, claimed)
        await _create_connector(test_client, admin_headers, unclaimed)

        installed.append(
            await _install_skill(test_client, admin_headers, claimed_skill, [claimed], created_mcp_slugs=[claimed])
        )
        assert (await _connector_state(postgres_sessions, claimed))["origin_skill"] == claimed_skill

        installed.append(await _install_skill(test_client, admin_headers, unclaimed_skill, [unclaimed]))
        assert (await _connector_state(postgres_sessions, unclaimed))["origin_skill"] is None
    finally:
        for slug in reversed(installed):
            await _delete_skill(test_client, admin_headers, slug)
        await _delete_connector(test_client, admin_headers, claimed)
        await _delete_connector(test_client, admin_headers, unclaimed)


async def test_delete_skill_disables_only_unreferenced_owned_connector(
    test_client, admin_headers, postgres_sessions
):
    """删除 Skill 时仍有其它引用则保留连接器，独占且无引用时停用。"""
    base = f"it-{uuid.uuid4().hex[:10]}"
    shared_connector = f"{base}-shared"
    solo_connector = f"{base}-solo"
    owner_skill = f"{base}-owner"
    other_skill = f"{base}-other"
    solo_skill = f"{base}-solo-skill"
    installed: list[str] = []
    try:
        await _create_connector(test_client, admin_headers, shared_connector)
        await _create_connector(test_client, admin_headers, solo_connector)

        installed.append(
            await _install_skill(
                test_client, admin_headers, owner_skill, [shared_connector], created_mcp_slugs=[shared_connector]
            )
        )
        installed.append(await _install_skill(test_client, admin_headers, other_skill, [shared_connector]))
        installed.append(
            await _install_skill(
                test_client, admin_headers, solo_skill, [solo_connector], created_mcp_slugs=[solo_connector]
            )
        )

        await _delete_skill(test_client, admin_headers, owner_skill)
        installed.remove(owner_skill)
        assert (await _connector_state(postgres_sessions, shared_connector))["enabled"] == 1

        await _delete_skill(test_client, admin_headers, other_skill)
        installed.remove(other_skill)
        assert (await _connector_state(postgres_sessions, shared_connector))["enabled"] == 1

        await _delete_skill(test_client, admin_headers, solo_skill)
        installed.remove(solo_skill)
        assert (await _connector_state(postgres_sessions, solo_connector))["enabled"] == 0
    finally:
        for slug in reversed(installed):
            await _delete_skill(test_client, admin_headers, slug)
        await _delete_connector(test_client, admin_headers, shared_connector)
        await _delete_connector(test_client, admin_headers, solo_connector)
