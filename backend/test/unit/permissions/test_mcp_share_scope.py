"""连接器共享范围的权限解析与范围校验。"""

from types import SimpleNamespace

import pytest

from yuxi.modules.extensions.mcp.models import MCPServer
from yuxi.modules.extensions.mcp.service import (
    allowed_mcp_access_levels,
    normalize_mcp_share_config,
)
from yuxi.modules.identity.permissions import ResourcePermission, resolve_mcp_permission


def _user(uid="user-1", role="user", department_id=1, is_deleted=0):
    return SimpleNamespace(uid=uid, role=role, department_id=department_id, is_deleted=is_deleted)


def _server(share_config=None, enabled=1, created_by="superadmin"):
    return MCPServer(
        slug="demo",
        name="Demo",
        transport="streamable_http",
        url="https://example.com/mcp",
        enabled=enabled,
        created_by=created_by,
        updated_by=created_by,
        share_config=share_config,
    )


def _scope(access_level, **kwargs):
    return {"version": 2, "read_scope": {"access_level": access_level, **kwargs}, "manage_scope": None}


def test_superadmin_manages_every_connector():
    assert resolve_mcp_permission(_user(role="superadmin"), _server()) == ResourcePermission.MANAGE


def test_admin_reads_global_connector_but_cannot_manage_it():
    server = _server(_scope("global"), created_by="superadmin")
    assert resolve_mcp_permission(_user(uid="admin-1", role="admin"), server) == ResourcePermission.READ


def test_admin_manages_own_department_connector():
    server = _server(_scope("department", department_ids=[1]), created_by="superadmin")
    admin = _user(uid="admin-1", role="admin", department_id=1)
    assert resolve_mcp_permission(admin, server) == ResourcePermission.MANAGE
    other = _user(uid="admin-2", role="admin", department_id=2)
    assert resolve_mcp_permission(other, server) == ResourcePermission.NONE


def test_user_reads_global_but_not_other_users_scope():
    assert resolve_mcp_permission(_user(), _server(_scope("global"))) == ResourcePermission.READ
    personal = _scope("user", user_uids=["someone-else"])
    assert resolve_mcp_permission(_user(), _server(personal)) == ResourcePermission.NONE


def test_department_scope_matches_only_single_department():
    server = _server(_scope("department", department_ids=[1]))
    assert resolve_mcp_permission(_user(department_id=1), server) == ResourcePermission.READ
    assert resolve_mcp_permission(_user(department_id=2), server) == ResourcePermission.NONE


def test_deleted_user_has_no_connector_permission():
    assert resolve_mcp_permission(_user(is_deleted=1), _server(_scope("global"))) == ResourcePermission.NONE


def test_allowed_levels_follow_role():
    assert allowed_mcp_access_levels(_user(role="superadmin")) == ["global", "department", "user"]
    assert allowed_mcp_access_levels(_user(role="admin")) == ["department", "user"]
    assert allowed_mcp_access_levels(_user(role="user")) == ["user"]


def test_admin_cannot_share_connector_globally():
    with pytest.raises(ValueError, match="共享范围"):
        normalize_mcp_share_config(_scope("global"), operator=_user(role="admin"))


def test_admin_default_scope_is_own_department():
    config = normalize_mcp_share_config(None, operator=_user(uid="admin-1", role="admin", department_id=3))
    assert config["read_scope"] == {"access_level": "department", "department_ids": [3], "user_uids": []}


def test_superadmin_can_share_connector_globally():
    config = normalize_mcp_share_config(_scope("global"), operator=_user(role="superadmin"))
    assert config["read_scope"]["access_level"] == "global"


@pytest.mark.asyncio
async def test_enabled_servers_filtered_by_share_scope(monkeypatch):
    from yuxi.modules.extensions.mcp import service as mcp_service

    hidden = _server(_scope("user", user_uids=["someone-else"]))
    hidden.slug = "hidden"
    visible = _server(_scope("global"))
    visible.slug = "visible"
    disabled = _server(_scope("global"), enabled=0)
    disabled.slug = "disabled"

    async def fake_list(db, names=None):
        return [hidden, visible, disabled]

    monkeypatch.setattr(mcp_service, "list_enabled_mcp_servers", fake_list)
    servers = await mcp_service.get_enabled_mcp_servers(db=SimpleNamespace(info={}), user=_user())
    assert [server.slug for server in servers] == ["visible"]


def test_serialize_hides_connection_details_from_admin():
    from yuxi.api.routers.extensions.mcp import serialize_mcp_server

    server = _server(_scope("global"))
    server.headers = {"Authorization": "Bearer secret"}
    admin_view = serialize_mcp_server(server, _user(role="admin"))
    assert admin_view["can_manage"] is False
    assert "url" not in admin_view and "headers" not in admin_view

    department_view = serialize_mcp_server(
        _server(_scope("department", department_ids=[1])), _user(role="admin", department_id=1)
    )
    assert department_view["can_manage"] is True

    superadmin_view = serialize_mcp_server(server, _user(role="superadmin"))
    assert superadmin_view["url"] == "https://example.com/mcp"
    assert superadmin_view["headers"] == {"Authorization": "Bearer secret"}
