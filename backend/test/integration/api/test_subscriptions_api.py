"""资源订阅申请与审批的真实 HTTP 集成验证。"""

from __future__ import annotations

import uuid

import pytest

from test.integration.api.helpers import create_api_user, delete_user_by_id

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]


def _share(access_level: str, **scope) -> dict:
    """构造 v2 共享范围。"""
    return {
        "version": 2,
        "read_scope": {"access_level": access_level, "department_ids": [], "user_uids": [], **scope},
        "manage_scope": None,
    }


async def _create_user(test_client, admin_headers) -> dict:
    """创建并登录一个普通用户。"""
    suffix = uuid.uuid4().hex[:8]
    password = f"Pw!{suffix}"
    user = await create_api_user(
        test_client,
        admin_headers,
        {"username": f"pytest_sub_{suffix}", "password": password, "role": "user"},
    )
    login = await test_client.post("/api/auth/token", data={"username": user["uid"], "password": password})
    assert login.status_code == 200, login.text
    return {"user": user, "headers": {"Authorization": f"Bearer {login.json()['access_token']}"}}


async def _create_connector(test_client, admin_headers, slug: str, scope: dict) -> None:
    """创建只有指定用户可见的连接器。"""
    response = await test_client.post(
        "/api/system/mcp-servers",
        headers=admin_headers,
        json={
            "slug": slug,
            "name": f"IT {slug}",
            "transport": "streamable_http",
            "url": "https://example.com/mcp",
            "timeout": 5,
            "sse_read_timeout": 5,
            "share_config": _share(**scope),
        },
    )
    assert response.status_code == 200, response.text


async def _visible_mcp_slugs(test_client, headers) -> set[str]:
    """读取连接器列表里的 slug 集合。"""
    response = await test_client.get("/api/system/mcp-servers", headers=headers)
    assert response.status_code == 200, response.text
    return {row["slug"] for row in response.json()["data"]}


async def _apply_slugs(test_client, admin_headers) -> set[str]:
    """读取订阅目录中当前用户不可见但可申请的资源。"""
    response = await test_client.get("/api/subscriptions/catalog", headers=admin_headers)
    assert response.status_code == 200, response.text
    return {row["resource_slug"] for row in response.json()["data"] if not row["visible"]}


async def _submit(test_client, headers, resource_type: str, slug: str) -> dict:
    """提交订阅申请。"""
    response = await test_client.post(
        "/api/subscriptions",
        headers=headers,
        json={"resource_type": resource_type, "resource_slug": slug},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def test_connector_subscription_becomes_visible_after_approval(test_client, admin_headers):
    """连接器申请由超管批准后进入可见列表，撤销后再次消失。"""
    user = await _create_user(test_client, admin_headers)
    slug = f"it-sub-mcp-{uuid.uuid4().hex[:8]}"
    try:
        await _create_connector(test_client, admin_headers, slug, {"access_level": "user", "user_uids": [uuid.uuid4().hex]})
        assert slug not in await _visible_mcp_slugs(test_client, user["headers"])
        assert slug in await _apply_slugs(test_client, user["headers"])

        created = await _submit(test_client, user["headers"], "mcp", slug)
        assert created["status"] == "pending"

        duplicate = await _submit(test_client, user["headers"], "mcp", slug)
        assert duplicate["id"] == created["id"]

        forbidden = await test_client.post(f"/api/subscriptions/{created['id']}/approve", headers=user["headers"])
        assert forbidden.status_code == 403, forbidden.text

        approved = await test_client.post(f"/api/subscriptions/{created['id']}/approve", headers=admin_headers)
        assert approved.status_code == 200, approved.text
        assert approved.json()["data"]["status"] == "approved"
        assert slug in await _visible_mcp_slugs(test_client, user["headers"])

        revoked = await test_client.delete(f"/api/subscriptions/{created['id']}", headers=admin_headers)
        assert revoked.status_code == 200, revoked.text
        assert slug not in await _visible_mcp_slugs(test_client, user["headers"])
    finally:
        await test_client.delete(f"/api/system/mcp-servers/{slug}", headers=admin_headers)
        await delete_user_by_id(test_client, admin_headers, user["user"]["id"])


async def test_rejected_subscription_can_be_resubmitted(test_client, admin_headers):
    """驳回后状态可回读，重新申请回到待审批而不是新建记录。"""
    user = await _create_user(test_client, admin_headers)
    slug = f"it-sub-rej-{uuid.uuid4().hex[:8]}"
    try:
        await _create_connector(test_client, admin_headers, slug, {"access_level": "user", "user_uids": [uuid.uuid4().hex]})
        created = await _submit(test_client, user["headers"], "mcp", slug)
        rejected = await test_client.post(f"/api/subscriptions/{created['id']}/reject", headers=admin_headers)
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["data"]["status"] == "rejected"

        mine = await test_client.get("/api/subscriptions/mine", headers=user["headers"])
        assert mine.status_code == 200, mine.text
        assert [row["status"] for row in mine.json()["data"]] == ["rejected"]

        again = await _submit(test_client, user["headers"], "mcp", slug)
        assert again["id"] == created["id"]
        assert again["status"] == "pending"
    finally:
        await test_client.delete(f"/api/system/mcp-servers/{slug}", headers=admin_headers)
        await delete_user_by_id(test_client, admin_headers, user["user"]["id"])


async def test_visible_resource_cannot_be_subscribed(test_client, admin_headers):
    """已在自己共享范围内的资源不进入可申请目录，提交时明确拒绝。"""
    user = await _create_user(test_client, admin_headers)
    slug = f"it-sub-visible-{uuid.uuid4().hex[:8]}"
    try:
        await _create_connector(
            test_client, admin_headers, slug, {"access_level": "user", "user_uids": [user["user"]["uid"]]}
        )
        scope = await test_client.put(
            f"/api/system/mcp-servers/{slug}/share-config",
            headers=admin_headers,
            json={"share_config": _share("global")},
        )
        assert scope.status_code == 200, scope.text
        assert slug not in await _apply_slugs(test_client, user["headers"])

        response = await test_client.post(
            "/api/subscriptions",
            headers=user["headers"],
            json={"resource_type": "mcp", "resource_slug": slug},
        )
        assert response.status_code == 400, response.text
        assert "已拥有" in response.json()["detail"]
    finally:
        await test_client.delete(f"/api/system/mcp-servers/{slug}", headers=admin_headers)
        await delete_user_by_id(test_client, admin_headers, user["user"]["id"])


async def test_private_agent_cannot_be_subscribed(test_client, admin_headers):
    """私有智能体不出现在目录，也不允许申请订阅。"""
    user = await _create_user(test_client, admin_headers)
    slug = f"it-sub-private-{uuid.uuid4().hex[:8]}"
    try:
        created = await test_client.post(
            "/api/agent",
            headers=admin_headers,
            json={"slug": slug, "name": slug, "visibility": "private"},
        )
        assert created.status_code == 200, created.text

        catalog = await test_client.get("/api/subscriptions/catalog", headers=user["headers"])
        assert catalog.status_code == 200, catalog.text
        assert slug not in {row["resource_slug"] for row in catalog.json()["data"]}

        response = await test_client.post(
            "/api/subscriptions",
            headers=user["headers"],
            json={"resource_type": "agent", "resource_slug": slug},
        )
        assert response.status_code == 400, response.text
        assert "私有智能体" in response.json()["detail"]
    finally:
        await test_client.delete(f"/api/agent/{slug}", headers=admin_headers)
        await delete_user_by_id(test_client, admin_headers, user["user"]["id"])


async def test_agent_subscription_grants_read_access(test_client, admin_headers):
    """共享智能体批准后，普通用户能在列表中看到它。"""
    user = await _create_user(test_client, admin_headers)
    slug = f"it-sub-agent-{uuid.uuid4().hex[:8]}"
    try:
        created = await test_client.post(
            "/api/agent",
            headers=admin_headers,
            json={
                "slug": slug,
                "name": slug,
                "visibility": "shared",
                "share_config": _share("user", user_uids=[uuid.uuid4().hex]),
            },
        )
        assert created.status_code == 200, created.text

        listing = await test_client.get("/api/agent", headers=user["headers"])
        assert listing.status_code == 200, listing.text
        assert slug not in {row["slug"] for row in listing.json()["agents"]}

        request = await _submit(test_client, user["headers"], "agent", slug)
        approved = await test_client.post(f"/api/subscriptions/{request['id']}/approve", headers=admin_headers)
        assert approved.status_code == 200, approved.text

        listing = await test_client.get("/api/agent", headers=user["headers"])
        assert listing.status_code == 200, listing.text
        assert slug in {row["slug"] for row in listing.json()["agents"]}
    finally:
        await test_client.delete(f"/api/agent/{slug}", headers=admin_headers)
        await delete_user_by_id(test_client, admin_headers, user["user"]["id"])
