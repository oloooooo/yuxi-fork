"""订阅审批按资源范围路由，以及个人建库权限的一次性授权。"""

from __future__ import annotations

import uuid

import pytest

from test.integration.api.helpers import create_department, delete_department_by_id
from test.support.knowledge import default_embedding_model_spec

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


async def _create_user(test_client, headers, *, role: str, department_id: int | None = None) -> dict:
    """创建用户并返回 uid/密码/请求头。"""
    suffix = uuid.uuid4().hex[:8]
    uid = f"sub_{role}_{suffix}"
    password = f"Pw!{suffix}"
    response = await test_client.post(
        "/api/auth/users",
        headers=headers,
        json={"username": uid, "password": password, "role": role, "department_id": department_id},
    )
    assert response.status_code == 200, response.text
    return {"uid": uid, "password": password, "headers": await _login(test_client, uid, password)}


async def _create_connector(test_client, headers, slug: str, share_config: dict) -> None:
    """建一个连接器。"""
    response = await test_client.post(
        "/api/system/mcp-servers",
        headers=headers,
        json={
            "slug": slug,
            "name": f"IT {slug}",
            "transport": "streamable_http",
            "url": "https://example.com/mcp",
            "share_config": share_config,
        },
    )
    assert response.status_code == 200, response.text


async def _submit(test_client, headers, resource_type: str, slug: str):
    """提交订阅申请。"""
    return await test_client.post(
        "/api/subscriptions",
        headers=headers,
        json={"resource_type": resource_type, "resource_slug": slug},
    )


async def test_department_admin_approves_only_own_department_requests(test_client, admin_headers):
    """部门管理员只看到并审批本部门范围的申请，指定人资源仍归超管。"""
    suffix = uuid.uuid4().hex[:6]
    department = await create_department(
        test_client,
        admin_headers,
        {"name": f"it-sub-dept-{suffix}", "admin_uid": f"sd_{suffix}", "admin_password": f"Pw!{suffix}"},
    )
    other_department = await create_department(
        test_client,
        admin_headers,
        {"name": f"it-sub-other-{suffix}", "admin_uid": f"so_{suffix}", "admin_password": f"Pw!{suffix}"},
    )
    department_admin = await _login(test_client, f"sd_{suffix}", f"Pw!{suffix}")
    other_admin = await _login(test_client, f"so_{suffix}", f"Pw!{suffix}")
    user = await _create_user(test_client, admin_headers, role="user")
    dept_slug = f"it-sub-dept-{suffix}"
    global_slug = f"it-sub-global-{suffix}"
    try:
        await _create_connector(
            test_client, admin_headers, dept_slug, _share("department", department_ids=[department["id"]])
        )
        await _create_connector(
            test_client, admin_headers, global_slug, _share("user", user_uids=[uuid.uuid4().hex])
        )

        dept_sub = await _submit(test_client, user["headers"], "mcp", dept_slug)
        assert dept_sub.status_code == 200, dept_sub.text
        global_sub = await _submit(test_client, user["headers"], "mcp", global_slug)
        assert global_sub.status_code == 200, global_sub.text

        listed = await test_client.get("/api/subscriptions/pending", headers=department_admin)
        assert listed.status_code == 200, listed.text
        pending_slugs = {item["resource_slug"] for item in listed.json()["data"]["items"]}
        assert dept_slug in pending_slugs
        assert global_slug not in pending_slugs

        other_listed = await test_client.get("/api/subscriptions/pending", headers=other_admin)
        assert other_listed.status_code == 200
        other_slugs = {item["resource_slug"] for item in other_listed.json()["data"]["items"]}
        assert other_slugs.isdisjoint({dept_slug, global_slug})

        denied_global = await test_client.post(
            f"/api/subscriptions/{global_sub.json()['data']['id']}/approve", headers=department_admin
        )
        assert denied_global.status_code == 403, denied_global.text
        denied_other = await test_client.post(
            f"/api/subscriptions/{dept_sub.json()['data']['id']}/approve", headers=other_admin
        )
        assert denied_other.status_code == 403, denied_other.text

        approved = await test_client.post(
            f"/api/subscriptions/{dept_sub.json()['data']['id']}/approve", headers=department_admin
        )
        assert approved.status_code == 200, approved.text
        await test_client.post(f"/api/subscriptions/{global_sub.json()['data']['id']}/approve", headers=admin_headers)

        visible = await test_client.get("/api/system/mcp-servers", headers=user["headers"])
        assert visible.status_code == 200
        assert dept_slug in {item["slug"] for item in visible.json()["data"]}
    finally:
        await test_client.delete(f"/api/system/mcp-servers/{dept_slug}", headers=admin_headers)
        await test_client.delete(f"/api/system/mcp-servers/{global_slug}", headers=admin_headers)
        await test_client.delete(f"/api/auth/users/{user['uid']}", headers=admin_headers)
        await delete_department_by_id(test_client, admin_headers, department["id"])
        await delete_department_by_id(test_client, admin_headers, other_department["id"])


async def test_knowledge_create_grant_is_approved_by_superadmin_only(test_client, admin_headers):
    """建库权限：部门管理员审批不了，超管批准后用户可建自己的知识库，且范围仅本人。"""
    suffix = uuid.uuid4().hex[:6]
    department = await create_department(
        test_client,
        admin_headers,
        {"name": f"it-kb-dept-{suffix}", "admin_uid": f"kd_{suffix}", "admin_password": f"Pw!{suffix}"},
    )
    department_admin = await _login(test_client, f"kd_{suffix}", f"Pw!{suffix}")
    applicant = await _create_user(test_client, admin_headers, role="user", department_id=department["id"])
    denied_user = await _create_user(test_client, admin_headers, role="user", department_id=department["id"])
    kb_id = None
    try:
        denied_create = await test_client.post(
            "/api/knowledge/knowledge-bases",
            headers=denied_user["headers"],
            json={
                "name": f"pytest_no_grant_{suffix}",
                "kb_type": "milvus",
                "description": "no grant",
                "embedding_model_spec": default_embedding_model_spec(),
            },
        )
        assert denied_create.status_code == 403, denied_create.text
        assert "知识库创建权限" in denied_create.text

        catalog = await test_client.get("/api/subscriptions/catalog", headers=applicant["headers"])
        assert catalog.status_code == 200
        assert "knowledge_create" in {item["resource_type"] for item in catalog.json()["data"]}

        applied = await _submit(test_client, applicant["headers"], "knowledge_create", "*")
        assert applied.status_code == 200, applied.text
        subscription_id = applied.json()["data"]["id"]

        denied_by_department = await test_client.post(
            f"/api/subscriptions/{subscription_id}/approve", headers=department_admin
        )
        assert denied_by_department.status_code == 403, denied_by_department.text

        approved = await test_client.post(f"/api/subscriptions/{subscription_id}/approve", headers=admin_headers)
        assert approved.status_code == 200, approved.text

        created = await test_client.post(
            "/api/knowledge/knowledge-bases",
            headers=applicant["headers"],
            json={
                "name": f"pytest_personal_{suffix}",
                "kb_type": "milvus",
                "description": "personal",
                "embedding_model_spec": default_embedding_model_spec(),
            },
        )
        assert created.status_code == 200, created.text
        kb_id = created.json()["kb_id"]

        status = await test_client.get(f"/api/knowledge/knowledge-bases/{kb_id}/status", headers=applicant["headers"])
        assert status.status_code in {200, 404}, status.text

        listed = await test_client.get("/api/subscriptions/mine", headers=applicant["headers"])
        assert listed.status_code == 200
        assert listed.json()["data"][0]["status"] == "approved"
    finally:
        if kb_id:
            await test_client.delete(f"/api/knowledge/knowledge-bases/{kb_id}", headers=admin_headers)
        await test_client.delete(f"/api/auth/users/{applicant['uid']}", headers=admin_headers)
        await test_client.delete(f"/api/auth/users/{denied_user['uid']}", headers=admin_headers)
        await delete_department_by_id(test_client, admin_headers, department["id"])
