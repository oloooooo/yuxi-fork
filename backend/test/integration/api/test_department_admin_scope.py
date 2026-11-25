"""部门管理员（admin）的真实 HTTP 权限边界：只治理本部门共享资源。"""

from __future__ import annotations

import uuid

import pytest

from test.integration.api.helpers import create_department, delete_department_by_id

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


def _share(access_level: str, **scope) -> dict:
    """构造 v2 共享范围。"""
    return {
        "version": 2,
        "read_scope": {"access_level": access_level, "department_ids": [], "user_uids": [], **scope},
        "manage_scope": None,
    }


async def _login(test_client, uid: str, password: str) -> dict[str, str]:
    """用账号密码换取请求头。"""
    response = await test_client.post("/api/auth/token", data={"username": uid, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _create_department_with_admin(test_client, headers: dict[str, str], name: str) -> tuple[dict, dict]:
    """创建部门（同时创建其管理员）并返回部门与管理员请求头。"""
    suffix = uuid.uuid4().hex[:8]
    uid = f"dept_admin_{suffix}"
    password = f"Pw!{suffix}"
    department = await create_department(
        test_client, headers, {"name": name, "admin_uid": uid, "admin_password": password}
    )
    return department, await _login(test_client, uid, password)


async def _create_connector(test_client, headers: dict[str, str], slug: str, share_config: dict | None) -> dict:
    """创建连接器并返回响应体。"""
    payload = {
        "slug": slug,
        "name": f"IT {slug}",
        "transport": "streamable_http",
        "url": "https://example.com/mcp",
        "timeout": 5,
        "sse_read_timeout": 5,
    }
    if share_config is not None:
        payload["share_config"] = share_config
    response = await test_client.post("/api/system/mcp-servers", headers=headers, json=payload)
    return response


async def _read_connector(test_client, headers: dict[str, str], slug: str) -> dict:
    """读取连接器详情。"""
    response = await test_client.get(f"/api/system/mcp-servers/{slug}", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def test_department_admin_governs_only_own_department_connectors(test_client, admin_headers):
    """部门管理员可删本部门连接器，不能删全局或其它部门的连接器，也不能设 global。"""
    suffix = uuid.uuid4().hex[:6]
    department, admin_headers_for_department = await _create_department_with_admin(
        test_client, admin_headers, f"it-dept-{suffix}"
    )
    other_department, _ = await _create_department_with_admin(test_client, admin_headers, f"it-dept-other-{suffix}")
    department_id = department["id"]
    other_department_id = other_department["id"]
    admin = {"headers": admin_headers_for_department}
    own_slug = f"it-dept-own-{suffix}"
    other_slug = f"it-dept-other-{suffix}"
    global_slug = f"it-dept-global-{suffix}"
    created_slug = f"it-dept-created-{suffix}"
    try:
        own = await _create_connector(
            test_client, admin_headers, own_slug, _share("department", department_ids=[department_id])
        )
        assert own.status_code == 200, own.text
        await _create_connector(
            test_client, admin_headers, other_slug, _share("department", department_ids=[other_department_id])
        )
        await _create_connector(test_client, admin_headers, global_slug, _share("global"))

        assert (await test_client.get(f"/api/system/mcp-servers/{own_slug}", headers=admin["headers"])).status_code == 200
        hidden = await test_client.get(f"/api/system/mcp-servers/{other_slug}", headers=admin["headers"])
        assert hidden.status_code in {403, 404}, hidden.text

        global_detail = await _read_connector(test_client, admin["headers"], global_slug)
        assert global_detail["can_manage"] is False
        assert "url" not in global_detail and "headers" not in global_detail

        denied_global_scope = await test_client.put(
            f"/api/system/mcp-servers/{own_slug}/share-config",
            headers=admin["headers"],
            json={"share_config": _share("global")},
        )
        assert denied_global_scope.status_code == 400, denied_global_scope.text

        denied_other_delete = await test_client.delete(f"/api/system/mcp-servers/{other_slug}", headers=admin["headers"])
        assert denied_other_delete.status_code == 403, denied_other_delete.text
        denied_global_delete = await test_client.delete(
            f"/api/system/mcp-servers/{global_slug}", headers=admin["headers"]
        )
        assert denied_global_delete.status_code == 403, denied_global_delete.text

        created = await _create_connector(test_client, admin["headers"], created_slug, None)
        assert created.status_code == 200, created.text
        detail = await _read_connector(test_client, admin["headers"], created_slug)
        scope = detail["share_config"]["read_scope"]
        assert scope["access_level"] == "department"
        assert scope["department_ids"] == [department_id]

        allowed_delete = await test_client.delete(f"/api/system/mcp-servers/{own_slug}", headers=admin["headers"])
        assert allowed_delete.status_code == 200, allowed_delete.text
    finally:
        for slug in (own_slug, other_slug, global_slug, created_slug):
            await test_client.delete(f"/api/system/mcp-servers/{slug}", headers=admin_headers)
        await delete_department_by_id(test_client, admin_headers, department_id)
        await delete_department_by_id(test_client, admin_headers, other_department_id)



async def test_department_admin_edits_non_sensitive_fields_and_resets_auth_only(test_client, admin_headers):
    """本部门管理员可改地址/超时/描述，认证只能整体重置；别部门管理员不能改。"""
    suffix = uuid.uuid4().hex[:6]
    department, creator_headers = await _create_department_with_admin(
        test_client, admin_headers, f"it-edit-dept-{suffix}"
    )
    other_department, peer_headers = await _create_department_with_admin(
        test_client, admin_headers, f"it-edit-peer-{suffix}"
    )
    department_id = department["id"]
    slug = f"it-edit-{suffix}"
    created = await test_client.post(
        "/api/system/mcp-servers",
        headers=creator_headers,
        json={
            "slug": slug,
            "name": f"IT {slug}",
            "transport": "streamable_http",
            "url": "https://example.com/mcp",
            "headers": {"Authorization": "Bearer old-token"},
            "auth_mode": "headers",
            "timeout": 5,
            "sse_read_timeout": 5,
            "share_config": _share("department", department_ids=[department_id]),
        },
    )
    assert created.status_code == 200, created.text
    peer_suffix = uuid.uuid4().hex[:8]
    peer_uid = f"dept_peer_{peer_suffix}"
    peer_password = f"Pw!{peer_suffix}"
    peer_created = await test_client.post(
        "/api/auth/users",
        headers=admin_headers,
        json={
            "username": peer_uid,
            "password": peer_password,
            "role": "admin",
            "department_id": department_id,
        },
    )
    assert peer_created.status_code == 200, peer_created.text
    same_department_headers = await _login(test_client, peer_uid, peer_password)
    try:
        peer_detail = await _read_connector(test_client, same_department_headers, slug)
        assert peer_detail["can_edit_config"] is True, "同部门管理员应能编辑配置"
        assert peer_detail["url"] == "https://example.com/mcp"
        assert "headers" not in peer_detail

        peer_update = await test_client.put(
            f"/api/system/mcp-servers/{slug}",
            headers=same_department_headers,
            json={"description": "同部门管理员改过"},
        )
        assert peer_update.status_code == 200, peer_update.text
        assert peer_update.json()["data"]["description"] == "同部门管理员改过"

        peer_denied_headers = await test_client.put(
            f"/api/system/mcp-servers/{slug}",
            headers=same_department_headers,
            json={"headers": {"Authorization": "Bearer leaked"}},
        )
        assert peer_denied_headers.status_code == 403, peer_denied_headers.text

        detail = await _read_connector(test_client, creator_headers, slug)
        assert detail["can_edit_config"] is True
        assert detail["url"] == "https://example.com/mcp"
        assert "headers" not in detail, "共享请求头不能回填给非超管"

        updated = await test_client.put(
            f"/api/system/mcp-servers/{slug}",
            headers=creator_headers,
            json={"description": "改过", "url": "https://example.com/mcp-v2", "timeout": 9},
        )
        assert updated.status_code == 200, updated.text
        after = updated.json()["data"]
        assert after["description"] == "改过" and after["url"] == "https://example.com/mcp-v2"
        assert after["timeout"] == 9
        assert (await _read_connector(test_client, admin_headers, slug))["headers"] == {
            "Authorization": "Bearer old-token"
        }, "未触碰认证时不应清空共享请求头"

        denied_headers = await test_client.put(
            f"/api/system/mcp-servers/{slug}",
            headers=creator_headers,
            json={"headers": {"Authorization": "Bearer leaked"}},
        )
        assert denied_headers.status_code == 403, denied_headers.text
        denied_transport = await test_client.put(
            f"/api/system/mcp-servers/{slug}", headers=creator_headers, json={"transport": "sse"}
        )
        assert denied_transport.status_code == 403, denied_transport.text

        reset = await test_client.put(
            f"/api/system/mcp-servers/{slug}",
            headers=creator_headers,
            json={"reset_auth": True, "auth_mode": "none", "headers": {}},
        )
        assert reset.status_code == 200, reset.text
        assert reset.json()["data"]["auth_mode"] == "none"
        assert (await _read_connector(test_client, admin_headers, slug))["headers"] == {}

        denied_peer = await test_client.put(
            f"/api/system/mcp-servers/{slug}", headers=peer_headers, json={"description": "peer"}
        )
        assert denied_peer.status_code in {403, 404}, denied_peer.text
    finally:
        await test_client.delete(f"/api/system/mcp-servers/{slug}", headers=admin_headers)
        await test_client.delete(f"/api/auth/users/{peer_uid}", headers=admin_headers)
        await delete_department_by_id(test_client, admin_headers, department_id)
        await delete_department_by_id(test_client, admin_headers, other_department["id"])


async def test_department_admin_can_assign_department_connector_to_specific_user(test_client, admin_headers):
    """部门管理员可把本部门连接器分配给指定用户，并继续保留管理权。"""
    suffix = uuid.uuid4().hex[:6]
    department, dept_admin_headers = await _create_department_with_admin(
        test_client, admin_headers, f"it-dept-assign-{suffix}"
    )
    department_id = department["id"]
    slug = f"it-dept-assign-{suffix}"
    try:
        created = await _create_connector(
            test_client, admin_headers, slug, _share("department", department_ids=[department_id])
        )
        assert created.status_code == 200, created.text

        target = await test_client.post(
            "/api/auth/users",
            headers=admin_headers,
            json={"username": f"assignee_{suffix}", "password": "Pw!assign1234", "role": "user"},
        )
        assert target.status_code == 200, target.text
        target_uid = target.json()["uid"]

        # 部门管理员把部门共享改成指定人（可以不是本部门的人），保存后仍然可以管理。
        to_user = await test_client.put(
            f"/api/system/mcp-servers/{slug}/share-config",
            headers=dept_admin_headers,
            json={"share_config": _share("user", user_uids=[target_uid])},
        )
        assert to_user.status_code == 200, to_user.text
        scope = to_user.json()["data"]["share_config"]
        assert scope["read_scope"]["user_uids"] == [target_uid]
        assert scope["owner_department_id"] == department_id

        detail = await _read_connector(test_client, dept_admin_headers, slug)
        assert detail["can_manage"] is True

        # 目标用户能看到这个连接器。
        target_headers = await _login(test_client, target_uid, "Pw!assign1234")
        visible = await test_client.get(f"/api/system/mcp-servers/{slug}", headers=target_headers)
        assert visible.status_code == 200, visible.text

        # 仍可以改回部门范围。
        back = await test_client.put(
            f"/api/system/mcp-servers/{slug}/share-config",
            headers=dept_admin_headers,
            json={"share_config": _share("department", department_ids=[department_id])},
        )
        assert back.status_code == 200, back.text
        assert back.json()["data"]["share_config"]["owner_department_id"] == department_id
    finally:
        await test_client.delete(f"/api/system/mcp-servers/{slug}", headers=admin_headers)
        await test_client.delete(f"/api/auth/users/{target_uid}", headers=admin_headers)
        await delete_department_by_id(test_client, admin_headers, department_id)
