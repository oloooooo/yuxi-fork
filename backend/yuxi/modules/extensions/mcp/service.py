"""远程 MCP 配置与工具入口：工具按运行装配，个人 OAuth 绑定真实运行用户。"""

from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.extensions.mcp.builtin import BUILTIN_MCP_DEFAULTS, BUILTIN_MCP_MANIFEST, builtin_mcp_available
from yuxi.modules.extensions.mcp.config import RemoteMCPConfig, normalize_mcp_manifest_entry
from yuxi.modules.extensions.mcp.models import MCPServer
from yuxi.modules.extensions.mcp.oauth_config import PERSONAL_AUTH_MODES, config_revision, configure_mcp_auth
from yuxi.modules.extensions.mcp.oauth_runtime import personal_mcp_tools
from yuxi.modules.extensions.mcp.repository import (
    delete_mcp_server_row,
    get_mcp_server,
    invalidate_mcp_connections,
    list_enabled_mcp_servers,
    personal_mcp_connections,
    save_mcp_server,
)
from yuxi.modules.extensions.mcp.runtime import (
    SharedMCPAuth,
)
from yuxi.modules.extensions.mcp.runtime import (
    get_mcp_tools as load_mcp_tools,
)

from yuxi.modules.identity.permissions import (
    ResourcePermission,
    normalize_permission_config,
    resolve_mcp_permission,
)

BUILTIN_MCP_SERVERS = BUILTIN_MCP_MANIFEST["mcpServers"]

_SYNCED_MCP_FIELDS = (
    "name",
    "description",
    "transport",
    "url",
    "headers",
    "timeout",
    "sse_read_timeout",
    "auth_mode",
)


class MCPServerNotFoundError(ValueError):
    """表示指定的 MCP 服务器不存在。"""


def allowed_mcp_access_levels(user) -> list[str]:
    """返回操作人可设定的连接器共享范围。"""
    if user.role == "superadmin":
        return ["global", "department", "user"]
    if user.role == "admin":
        return ["department", "user"]
    return ["user"]


def default_mcp_share_config(user) -> dict:
    """新连接器默认范围：超管全平台，管理员仅本人。"""
    scope = (
        {"access_level": "global", "department_ids": [], "user_uids": []}
        if user.role == "superadmin"
        else {"access_level": "user", "department_ids": [], "user_uids": [user.uid]}
    )
    return {"version": 2, "read_scope": scope, "manage_scope": None}


def normalize_mcp_share_config(share_config: dict | None, *, operator) -> dict:
    """校验并标准化连接器共享范围，越权范围显式失败。"""
    return normalize_permission_config(
        share_config or default_mcp_share_config(operator),
        allowed_access_levels=set(allowed_mcp_access_levels(operator)),
        unauthorized_access_level_message="当前用户无权使用该连接器共享范围",
        strict=True,
    )


def mcp_server_visible(user, server: MCPServer) -> bool:
    """判断已启用的连接器是否对当前用户可读。"""
    return bool(server.enabled) and resolve_mcp_permission(user, server) != ResourcePermission.NONE


def is_builtin_mcp_server(server: MCPServer) -> bool:
    """判断 MCP 是否由代码中的内置定义管理。"""
    return server.slug in BUILTIN_MCP_SERVERS


# =============================================================================
# === 内置同步与配置读取 ===
# =============================================================================


async def ensure_builtin_mcp_servers_in_db() -> None:
    """同步代码拥有的连接字段，保留管理员启停和工具开关。"""
    from yuxi.infrastructure.postgres.manager import pg_manager

    async with pg_manager.get_async_session_context() as session:
        any_changed = False

        for slug, entry in BUILTIN_MCP_SERVERS.items():
            config = {
                **normalize_mcp_manifest_entry(slug, entry),
                **BUILTIN_MCP_DEFAULTS.get(slug, {"auth_mode": "headers", "enabled": False}),
            }
            result = await session.execute(select(MCPServer).filter(MCPServer.slug == slug))
            existing = result.scalar_one_or_none()
            if not existing:
                session.add(
                    MCPServer(
                        slug=slug,
                        name=config.get("name", slug),
                        description=config.get("description"),
                        transport=config["transport"],
                        url=config.get("url"),
                        headers=config.get("headers"),
                        timeout=config.get("timeout"),
                        sse_read_timeout=config.get("sse_read_timeout"),
                        # delegation 表示凭据由 Agent 运行时按当前用户签发；enabled 表达清单声明的默认启停。
                        auth_mode=config.get("auth_mode", "headers"),
                        enabled=1 if config.get("enabled") else 0,
                        created_by="system",
                        updated_by="system",
                        share_config={
                            "version": 2,
                            "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
                            "manage_scope": None,
                        },
                    )
                )
                any_changed = True
                logger.info(f"Added built-in MCP server '{slug}' to database")
                continue

            if existing.created_by != "system":
                raise ValueError(f"内置 MCP slug 冲突：{slug} 已由用户服务占用，请先显式处理")

            server_changed = False
            for field in _SYNCED_MCP_FIELDS:
                next_value = config.get(field)
                if getattr(existing, field) != next_value:
                    setattr(existing, field, next_value)
                    server_changed = True
            if server_changed:
                existing.updated_by = "system"
                any_changed = True

        if any_changed:
            await session.commit()


async def get_enabled_mcp_server_config(server_slug: str, *, db: AsyncSession | None = None) -> dict[str, Any] | None:
    """Get the latest enabled MCP server config from the database."""
    servers = await get_enabled_mcp_servers(names=[server_slug], db=db)
    return _to_runtime_mcp_config(servers[0]) if servers else None


async def get_enabled_mcp_server_slugs(*, db: AsyncSession | None = None) -> list[str]:
    """读取受支持且已启用的服务标识，供配置选择与 Skill 依赖校验。"""
    return [server.slug for server in await get_enabled_mcp_servers(db=db)]


async def get_enabled_mcp_servers(
    *, names: list[str] | None = None, db: AsyncSession | None = None, user=None
) -> list[MCPServer]:
    """统一读取可配置的启用服务；传入用户时只保留其可见范围。"""
    if db is None:
        from yuxi.infrastructure.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as session:
            return await get_enabled_mcp_servers(names=names, db=session, user=user)

    servers = [server for server in await list_enabled_mcp_servers(db, names=names) if builtin_mcp_available(server.slug)]
    if user is None:
        return servers
    return [server for server in servers if mcp_server_visible(user, server)]


async def get_mcp_tools(
    server_slug: str,
    additional_servers: dict[str, dict[str, Any]] | None = None,
    disabled_tools: list[str] | None = None,
) -> list[Callable[..., Any]]:
    """共享 headers 的底层入口；个人授权必须经 get_enabled_mcp_tools 传入可信 uid。"""
    if additional_servers and server_slug in additional_servers:
        config = additional_servers[server_slug]
    else:
        servers = await get_enabled_mcp_servers(names=[server_slug])
        server = servers[0] if servers else None
        if server is not None and server.auth_mode not in {"headers", "none"}:
            raise ValueError("个人授权或平台委托的 MCP 必须经可信用户身份加载")
        config = _to_runtime_mcp_config(server) if server is not None else None
    if config and config.get("auth_mode") in PERSONAL_AUTH_MODES:
        raise ValueError("个人 MCP 必须经可信用户身份加载")
    if config and config.get("auth_mode") == "delegation":
        raise ValueError("平台委托 MCP 由 Agent 运行时按当前用户装配，不支持共享连接加载")
    if config and "auth" in config:
        raise ValueError("共享 headers 连接不允许自带 auth，出站策略由装配层注入")
    return await load_mcp_tools(server_slug, config, disabled_tools=disabled_tools)


# =============================================================================
# === Server Config CRUD ===
# =============================================================================


async def create_mcp_server(
    db: AsyncSession,
    slug: str,
    name: str,
    transport: str,
    url: str = None,
    description: str = None,
    headers: dict = None,
    timeout: int = None,
    sse_read_timeout: int = None,
    created_by: str = None,
    auth_mode: str = "headers",
    oauth_config: dict | None = None,
    api_key_header: str | None = None,
    operator=None,
    share_config: dict | None = None,
) -> MCPServer:
    """创建并独立发布远程 MCP 配置。"""
    if slug in BUILTIN_MCP_SERVERS:
        raise ValueError("系统内置连接器的 slug 由代码保留，无法通过接口创建")
    config = RemoteMCPConfig.model_validate(
        dict(
            slug=slug,
            name=name,
            transport=transport,
            url=url,
            description=description,
            headers=headers,
            timeout=timeout,
            sse_read_timeout=sse_read_timeout,
        )
    ).model_dump()

    existing = await get_mcp_server(db, slug)
    if existing:
        raise ValueError(f"Server slug '{slug}' already exists")

    server = MCPServer(
        **config,
        enabled=1,
        created_by=created_by,
        updated_by=created_by,
        share_config=(
            normalize_mcp_share_config(share_config, operator=operator) if operator is not None else None
        ),
    )
    configure_mcp_auth(server, auth_mode, oauth_config, api_key_header)
    await save_mcp_server(db, server, new=True)
    await db.commit()
    await db.refresh(server)
    logger.info(f"Created MCP server '{slug}'")
    return server


async def update_mcp_share_config(db: AsyncSession, slug: str, share_config: dict | None, *, operator) -> MCPServer:
    """按操作人权限更新连接器共享范围，内置连接器由代码管理。"""
    server = await get_mcp_server(db, slug, lock=True)
    if not server:
        raise MCPServerNotFoundError(f"Server '{slug}' does not exist")
    if is_builtin_mcp_server(server):
        raise PermissionError("系统内置连接器的共享范围由代码管理")
    if resolve_mcp_permission(operator, server) != ResourcePermission.MANAGE:
        raise PermissionError("仅管理员可修改连接器共享范围")
    server.share_config = normalize_mcp_share_config(share_config, operator=operator)
    server.updated_by = operator.username
    await db.commit()
    await db.refresh(server)
    logger.info(f"Updated MCP share config '{slug}'")
    return server


async def sync_mcp_share_config_for_skill(
    db: AsyncSession, *, slugs: list[str], skill_slug: str, share_config: dict | None, operator
) -> None:
    """只让该 Skill 专有的连接器共享范围跟随 Skill 授权，事务由调用方提交。"""
    if not slugs:
        return
    from yuxi.modules.identity.permissions import ResourcePermission, resolve_mcp_permission

    normalized = normalize_mcp_share_config(share_config, operator=operator)
    for slug in sorted(dict.fromkeys(slugs)):
        server = await get_mcp_server(db, slug, lock=True)
        if not server or is_builtin_mcp_server(server) or server.origin_skill != skill_slug:
            continue
        if resolve_mcp_permission(operator, server) != ResourcePermission.MANAGE:
            logger.warning(f"Skip syncing share config of MCP '{slug}': operator lacks manage permission")
            continue
        server.share_config = normalized
        server.updated_by = operator.username
        await save_mcp_server(db, server)


async def list_mcp_dependents(db: AsyncSession, slug: str) -> dict[str, list[dict[str, str]]]:
    """列出依赖该连接器的 Skill 与智能体，供停用/删除前提示。"""
    from yuxi.modules.agents.repositories.definitions import AgentRepository
    from yuxi.modules.extensions.skills.repository import SkillRepository

    skills = await SkillRepository(db).list_referencing_mcp(slug)
    agents = await AgentRepository(db).list_referencing_mcp(slug)
    return {
        "skills": [{"slug": item.slug, "name": item.name} for item in skills],
        "agents": [{"slug": item.slug, "name": item.name} for item in agents],
    }


async def mark_skill_owned_mcp_servers(
    db: AsyncSession, *, slugs: list[str], claimed_slugs: set[str], skill_slug: str, operator
) -> None:
    """认领本次新建的连接器：仅限操作人自建、尚无归属且在依赖列表内的标识。"""
    for slug in sorted(dict.fromkeys(slugs)):
        server = await get_mcp_server(db, slug, lock=True)
        if not server or is_builtin_mcp_server(server):
            continue
        if server.origin_skill == skill_slug:
            continue
        if slug not in claimed_slugs or server.origin_skill is not None:
            continue
        if server.created_by != operator.username:
            continue
        server.origin_skill = skill_slug
        server.updated_by = operator.username
        await save_mcp_server(db, server)


async def release_skill_owned_mcp_servers(
    db: AsyncSession, *, mcp_slugs: list[str], skill_slug: str, skill_id: int
) -> None:
    """删除 Skill 后停用它专有且已无引用的连接器，事务由调用方提交。"""
    from yuxi.modules.agents.repositories.definitions import AgentRepository
    from yuxi.modules.extensions.skills.repository import SkillRepository

    skill_repo = SkillRepository(db)
    agent_repo = AgentRepository(db)
    for slug in sorted(dict.fromkeys(mcp_slugs)):
        server = await get_mcp_server(db, slug, lock=True)
        if not server or is_builtin_mcp_server(server) or server.origin_skill != skill_slug:
            continue
        if await skill_repo.list_referencing_mcp(slug, exclude_id=skill_id):
            continue
        if await agent_repo.list_referencing_mcp(slug):
            continue
        server.enabled = 0
        await save_mcp_server(db, server)


async def update_mcp_server(
    db: AsyncSession,
    slug: str,
    name: str = None,
    description: str = None,
    transport: str = None,
    url: str = None,
    headers: dict = None,
    timeout: int = None,
    sse_read_timeout: int = None,
    updated_by: str = None,
    auth_mode: str | None = None,
    oauth_config: dict | None = None,
    api_key_header: str | None = None,
) -> MCPServer:
    """在服务锁内更新配置，并使旧修订的个人连接失效。"""
    server = await get_mcp_server(db, slug, lock=True)
    if not server:
        raise MCPServerNotFoundError(f"Server '{slug}' does not exist")
    if is_builtin_mcp_server(server):
        raise PermissionError("系统内置连接器的连接配置由代码管理，无法通过接口修改")

    if server.auth_mode == "headers" and auth_mode in PERSONAL_AUTH_MODES | {"none"} and server.headers and headers is None:
        raise ValueError("切换接入方式前请显式清空或替换共享请求头")

    previous_revision = config_revision(server)
    updates = dict(
        name=name,
        description=description,
        transport=transport,
        url=url,
        headers=headers,
        timeout=timeout,
        sse_read_timeout=sse_read_timeout,
    )
    current = {field: getattr(server, field) for field in RemoteMCPConfig.model_fields}
    RemoteMCPConfig.model_validate({**current, **{key: value for key, value in updates.items() if value is not None}})

    for field, value in updates.items():
        if value is not None:
            setattr(server, field, value)
    if updated_by is not None:
        server.updated_by = updated_by

    configure_mcp_auth(server, auth_mode or server.auth_mode or "headers", oauth_config, api_key_header)
    if config_revision(server) != previous_revision:
        await invalidate_mcp_connections(db, server.id)
    await db.commit()
    await db.refresh(server)

    logger.info(f"Updated MCP server '{slug}'")
    return server


async def delete_mcp_server(db: AsyncSession, slug: str) -> bool:
    """删除 MCP 服务器，内置服务器由代码管理。"""
    server = await get_mcp_server(db, slug)
    if not server:
        return False
    if is_builtin_mcp_server(server):
        raise PermissionError("系统内置连接器的连接配置由代码管理，无法删除")

    await delete_mcp_server_row(db, server)
    await db.commit()

    logger.info(f"Deleted MCP server '{slug}'")
    return True


# =============================================================================
# === Tool Management ===
# =============================================================================


async def set_server_enabled(db: AsyncSession, slug: str, enabled: bool, updated_by: str = None) -> tuple[bool, MCPServer]:
    """Set server enabled status."""
    server = await get_mcp_server(db, slug)
    if not server:
        raise MCPServerNotFoundError(f"Server '{slug}' does not exist")
    server.enabled = 1 if enabled else 0
    if updated_by is not None:
        server.updated_by = updated_by
    await db.commit()

    is_enabled = bool(server.enabled)

    logger.info(f"Set MCP server '{slug}' enabled={is_enabled}")
    return is_enabled, server


async def toggle_tool_enabled(
    db: AsyncSession,
    server_slug: str,
    tool_name: str,
    updated_by: str = None,
) -> tuple[bool, MCPServer]:
    """Toggle single tool enabled status.

    Args:
        db: Database session
        server_slug: Server slug
        tool_name: Tool name
        updated_by: Updater

    Returns:
        (enabled, server): Tool enabled status and updated server object
    """
    server = await get_mcp_server(db, server_slug)
    if not server:
        raise MCPServerNotFoundError(f"Server '{server_slug}' does not exist")

    disabled_tools = list(server.disabled_tools or [])

    if tool_name in disabled_tools:
        disabled_tools.remove(tool_name)
        enabled = True
    else:
        disabled_tools.append(tool_name)
        enabled = False

    server.disabled_tools = disabled_tools
    if updated_by is not None:
        server.updated_by = updated_by
    await db.commit()

    logger.info(f"Toggled tool '{tool_name}' for server '{server_slug}' enabled={enabled}")
    return enabled, server


# =============================================================================
# === 按调用身份装配工具 ===
# =============================================================================


async def resolve_runtime_mcp_tools(
    server_slugs: list[str],
    *,
    uid: str | None,
    run_tools: dict[str, list] | None = None,
    unavailable: dict[str, str] | None = None,
    context=None,
) -> dict[str, list]:
    """按本人当前权限读取工具，本 Run 的目录由调用方独占持有，不跨运行或用户复用。"""
    import asyncio

    from yuxi.infrastructure.postgres.manager import pg_manager
    from yuxi.modules.extensions.mcp.repository import active_mcp_user

    if not server_slugs:
        return {}
    async with pg_manager.get_async_session_context() as db:
        user = await active_mcp_user(db, uid=uid)
        servers = await available_mcp_servers(db, server_slugs, user_id=user.id)

    if run_tools is None:
        run_tools = {}

    if unavailable is None:
        unavailable = {}
    for slug in server_slugs:
        if slug not in servers:
            unavailable[slug] = "未连接或不可用，请在扩展页面检查连接"

    async def load(server):
        """隔离单服务发现故障，日志和提示只包含安全原因。"""
        try:
            if server.auth_mode in PERSONAL_AUTH_MODES:
                return await personal_mcp_tools(server, uid, include_disabled=True)
            config = _to_runtime_mcp_config(server)
            if server.auth_mode == "delegation":
                config.pop("headers", None)
                config.update(_delegation_connection(server, uid=uid, context=context))
                return await load_mcp_tools(server.slug, config, disabled_tools=[])
            return await get_mcp_tools(server.slug, additional_servers={server.slug: config}, disabled_tools=[])
        except Exception as exc:
            reason, permanent = runtime_mcp_failure(exc)
            if permanent:
                unavailable[server.slug] = reason
            logger.warning(
                "MCP discovery unavailable: server={}, uid={}, error_type={}, reason={}",
                server.slug,
                uid,
                type(exc).__name__,
                reason,
            )
            return None

    pending = {slug: server for slug, server in servers.items() if slug not in run_tools and slug not in unavailable}
    loaded = await asyncio.gather(*(load(server) for server in pending.values()))
    run_tools.update({slug: tools for slug, tools in zip(pending, loaded, strict=True) if tools is not None})
    return {
        slug: [tool for tool in run_tools[slug] if tool.name not in (server.disabled_tools or [])]
        for slug, server in servers.items()
        if slug in run_tools and slug not in unavailable
    }


def runtime_mcp_failure(exc: Exception) -> tuple[str, bool]:
    """统一解释 MCP 错误；只有确定的凭据配置问题在本次运行禁用服务。"""
    from langgraph.errors import GraphBubbleUp

    from yuxi.modules.knowledge.mcp.client import KnowledgeMCPRuntimeError
    from yuxi.shared.errors import PermissionDeniedError, StateConflictError

    if isinstance(exc, (PermissionError, PermissionDeniedError, GraphBubbleUp)):
        raise exc
    if isinstance(exc, KnowledgeMCPRuntimeError):
        return "运行上下文缺少可信知识库范围，能力未装配", True
    if isinstance(exc, StateConflictError):
        if exc.code == "mcp_remote_forbidden":
            return "连接器服务方权限不足，请检查个人账号权限", False
        if exc.code == "mcp_oauth_unconfigured":
            return "部署未配置有效的连接器凭据加密密钥，请联系管理员", True
        if exc.code == "mcp_reconnect_required":
            return "授权凭据不可用或已失效，请在扩展页面重新连接", True
    if isinstance(exc, ExceptionGroup):
        failures = [runtime_mcp_failure(child) for child in exc.exceptions]
        return "；".join(dict.fromkeys(reason for reason, _ in failures)), any(permanent for _, permanent in failures)
    if isinstance(exc.__cause__, Exception):
        return runtime_mcp_failure(exc.__cause__)
    return "服务连接或调用失败，请检查连接器后重试", False


async def available_mcp_servers(db: AsyncSession, server_slugs, *, user_id: int) -> dict[str, MCPServer]:
    """统一解析服务开关、可见范围与本人的连接，返回可用服务且保留请求顺序。"""
    from yuxi.modules.extensions.mcp.oauth_service import connection_status
    from yuxi.modules.extensions.mcp.repository import active_mcp_user, runtime_mcp_servers

    if not server_slugs:
        return {}
    user = await active_mcp_user(db, user_id=user_id)
    rows = await runtime_mcp_servers(db, server_slugs, user_id)
    available = {
        server.slug: server
        for server, connection in rows
        if mcp_server_visible(user, server)
        and builtin_mcp_available(server.slug)
        and connection_status(server, connection) in {"connected", "not_required"}
    }
    return {slug: available[slug] for slug in dict.fromkeys(server_slugs) if slug in available}


async def personal_connection_states(db: AsyncSession, servers: list[MCPServer], user_id: int) -> dict[str, str]:
    """连接状态的展示投影由 service 统一解释，repository 只读取本人连接。"""
    from yuxi.modules.extensions.mcp.oauth_service import connection_status

    own = await personal_mcp_connections(db, [server.id for server in servers], user_id)
    return {server.slug: connection_status(server, own.get(server.id)) for server in servers}


async def get_enabled_mcp_tools(server_slug: str, *, uid: str | None = None) -> list:
    """单服务按当前用户装配个人凭证，无认证与共享连接沿用现有路径。"""
    servers = await get_enabled_mcp_servers(names=[server_slug])
    if not servers:
        return []
    server = servers[0]
    if server.auth_mode in PERSONAL_AUTH_MODES:
        return await personal_mcp_tools(server, uid)
    if server.auth_mode == "delegation":
        raise ValueError("平台委托 MCP 由 Agent 运行时按当前用户装配，不支持单服务直接加载")
    config = _to_runtime_mcp_config(server)
    return await get_mcp_tools(server_slug, additional_servers={server_slug: config}, disabled_tools=server.disabled_tools or [])


async def get_all_mcp_tools(server_slug: str, *, uid: str | None = None) -> list:
    """管理页工具清单按当前配置建连；停用只影响运行装配，不隐藏目录。"""
    from yuxi.infrastructure.postgres.manager import pg_manager

    async with pg_manager.get_async_session_context() as db:
        server = await get_mcp_server(db, server_slug)
    if server is None:
        raise MCPServerNotFoundError(f"服务器 '{server_slug}' 不存在")
    return await inspect_mcp_server_tools(server, uid=uid)


async def inspect_mcp_server_tools(server: MCPServer, *, uid: str | None = None) -> list:
    """管理端严格建连，包括停用服务；不吞异常、不修改运行状态。"""
    if not builtin_mcp_available(server.slug):
        raise ValueError("连接器在当前部署不可用")
    if server.auth_mode in PERSONAL_AUTH_MODES:
        return await personal_mcp_tools(server, uid, include_disabled=True, management=True)
    config = _to_runtime_mcp_config(server)
    config["auth"] = None
    if getattr(server, "auth_mode", "headers") == "delegation":
        config.pop("headers", None)
        config.update(_delegation_connection(server, uid=uid, management=True))
    return await load_mcp_tools(server.slug, config, disabled_tools=[])


def _delegation_connection(server, *, uid, context=None, management=False):
    """内部服务直接提供 SDK 认证，执行请求复用通用出站策略。"""
    from yuxi.modules.knowledge.mcp.client import knowledge_mcp_connection

    policy = None if management else SharedMCPAuth(server.slug, server.to_mcp_config(), uid=uid, auth_mode=server.auth_mode)
    return knowledge_mcp_connection(
        server.slug,
        uid=uid,
        context=context,
        management=management,
        before_request=policy.validate_request if policy is not None else None,
    )


def _to_runtime_mcp_config(server: MCPServer) -> dict[str, Any]:
    """生成运行时 MCP 配置，内置连接字段始终以代码定义为准。"""
    if not is_builtin_mcp_server(server):
        config = server.to_mcp_config()
        if server.auth_mode != "headers":
            config["auth_mode"] = server.auth_mode
        return config

    builtin = normalize_mcp_manifest_entry(server.slug, BUILTIN_MCP_SERVERS[server.slug])
    config = {key: builtin[key] for key in ("transport", "url", "headers", "timeout", "sse_read_timeout") if builtin.get(key) is not None}
    if server.disabled_tools:
        config["disabled_tools"] = server.disabled_tools
    return config
