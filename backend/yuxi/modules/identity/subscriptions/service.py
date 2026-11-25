"""资源订阅申请：资源目录、申请与超管审批。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.agents.models.definitions import Agent
from yuxi.modules.agents.repositories.definitions import AgentRepository
from yuxi.modules.extensions.mcp.models import MCPServer
from yuxi.modules.extensions.skills.models import Skill
from yuxi.modules.extensions.skills.repository import SkillRepository
from yuxi.modules.identity.models import User
from yuxi.modules.identity.permissions import (
    ADMIN_ROLES,
    ResourcePermission,
    resolve_agent_permission,
    resolve_mcp_permission,
    resolve_skill_permission,
)
from yuxi.modules.identity.subscriptions.models import ResourceSubscription
from yuxi.modules.identity.subscriptions.repository import SubscriptionRepository
from yuxi.shared.datetime import utc_now

RESOURCE_TYPES = {"mcp": "连接器", "agent": "智能体", "skill": "Skill"}
KNOWLEDGE_CREATE_TYPE = "knowledge_create"
KNOWLEDGE_CREATE_SLUG = "*"
KNOWLEDGE_CREATE_LABEL = "个人知识库创建权限"


class SubscriptionError(ValueError):
    """申请人可见的业务错误，由路由转成 4xx。"""

    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


async def _resource_row(db: AsyncSession, resource_type: str, slug: str) -> Agent | Skill | MCPServer | None:
    """按类型读取资源原始行，用来确认存在性与展示名。"""
    if resource_type == "mcp":
        return await db.scalar(select(MCPServer).where(MCPServer.slug == slug))
    if resource_type == "agent":
        return await AgentRepository(db).get_by_slug(slug)
    if resource_type == "skill":
        return await SkillRepository(db).get_by_slug(slug)
    raise SubscriptionError(f"未知资源类型：{resource_type}")


def _approver_can_handle(row: Agent | Skill | MCPServer | None, operator: User) -> bool:
    """超管审批全部；部门范围的资源由对应部门管理员审批。"""
    if operator.role == "superadmin":
        return True
    if operator.role != "admin" or row is None or not operator.department_id:
        return False
    read_scope = (row.share_config or {}).get("read_scope") or {}
    if read_scope.get("access_level") != "department":
        return False
    return operator.department_id in (read_scope.get("department_ids") or [])


def _current_permission(
    user: User, row: Agent | Skill | MCPServer, *, granted: bool = False
) -> ResourcePermission:
    """判断当前用户对资源的使用权限；granted 表示已有批准的订阅。"""
    if isinstance(row, Agent):
        return resolve_agent_permission(user, row, granted=granted)
    if isinstance(row, Skill):
        return resolve_skill_permission(user, row, granted=granted)
    return resolve_mcp_permission(user, row, granted=granted)


def serialize(row: ResourceSubscription) -> dict[str, Any]:
    """对外输出不携带内部外键。"""
    return {
        "id": row.id,
        "resource_type": row.resource_type,
        "resource_slug": row.resource_slug,
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "decided_by": row.decided_by,
    }


async def _reconcile_access(db: AsyncSession, user: User, items: list[ResourceSubscription]) -> None:
    """角色或共享范围变化后，申请人已直接获得权限的待审批记录转为已批准。"""
    changed = False
    for item in items:
        if item.status != "pending" or item.resource_type == KNOWLEDGE_CREATE_TYPE:
            continue
        row = await _resource_row(db, item.resource_type, item.resource_slug)
        if row is None or _current_permission(user, row) == ResourcePermission.NONE:
            continue
        item.status = "approved"
        item.decided_at = utc_now()
        item.decided_by = "system:reconcile"
        changed = True
    if changed:
        await db.commit()


async def list_catalog(db: AsyncSession, user: User) -> list[dict[str, Any]]:
    """列出全部可申请资源（只含名称与描述），并标注当前用户的可见性与申请状态。"""
    repo = SubscriptionRepository(db)
    items = await repo.list_mine(user.id)
    await _reconcile_access(db, user, items)
    mine = {row.resource_slug: row for row in items}
    rows: list[tuple[str, Agent | Skill | MCPServer]] = []
    for agent in await db.scalars(select(Agent).order_by(Agent.id.asc())):
        rows.append(("agent", agent))
    for skill in await db.scalars(select(Skill).order_by(Skill.id.asc())):
        rows.append(("skill", skill))
    for server in await db.scalars(select(MCPServer).order_by(MCPServer.id.asc())):
        rows.append(("mcp", server))

    catalog = []
    grant = mine.get(KNOWLEDGE_CREATE_SLUG)
    if user.role not in ADMIN_ROLES:
        catalog.append(
            {
                "resource_type": KNOWLEDGE_CREATE_TYPE,
                "resource_slug": KNOWLEDGE_CREATE_SLUG,
                "name": KNOWLEDGE_CREATE_LABEL,
                "description": "批准后可以创建属于你个人的知识库",
                "visible": grant is not None and grant.status == "approved",
                "status": grant.status if grant else None,
                "subscription_id": grant.id if grant else None,
            }
        )
    for resource_type, row in rows:
        if resource_type == "agent" and row.visibility == "private":
            continue
        subscription = mine.get(row.slug)
        granted = subscription is not None and subscription.status == "approved"
        catalog.append(
            {
                "resource_type": resource_type,
                "resource_slug": row.slug,
                "name": row.name,
                "description": getattr(row, "description", None) or "",
                "visible": _current_permission(user, row, granted=granted) != ResourcePermission.NONE,
                "status": subscription.status if subscription else None,
                "subscription_id": subscription.id if subscription else None,
            }
        )
    catalog.sort(key=lambda item: (item["resource_type"], item["name"], item["resource_slug"]))
    return catalog


async def submit_request(db: AsyncSession, user: User, resource_type: str, slug: str) -> ResourceSubscription:
    """登记订阅申请；已可见或重复申请时给出稳定结果。"""
    if resource_type == KNOWLEDGE_CREATE_TYPE:
        if user.role in ADMIN_ROLES:
            raise SubscriptionError("管理员无需申请知识库创建权限")
        return await _submit_knowledge_create(db, user)
    row = await _resource_row(db, resource_type, slug)
    if row is None:
        raise SubscriptionError("资源不存在", status_code=404)
    if resource_type == "agent" and row.visibility == "private":
        raise SubscriptionError("私有智能体不支持订阅")
    if _current_permission(user, row) != ResourcePermission.NONE:
        raise SubscriptionError("你已拥有该资源的使用权限")

    repo = SubscriptionRepository(db)
    existing = await repo.find(resource_type=resource_type, resource_slug=slug, user_id=user.id)
    if existing is not None:
        if existing.status == "pending":
            return existing
        if existing.status == "approved":
            return existing
        existing.status = "pending"
        existing.decided_at = None
        existing.decided_by = None
        await db.commit()
        await db.refresh(existing)
        return existing

    item = ResourceSubscription(
        resource_type=resource_type,
        resource_slug=slug,
        user_id=user.id,
        status="pending",
    )
    item = await repo.save(item)
    await db.commit()
    await db.refresh(item)
    return item


async def _submit_knowledge_create(db: AsyncSession, user: User) -> ResourceSubscription:
    """建库权限申请一人一条，驳回后可重新申请。"""
    repo = SubscriptionRepository(db)
    existing = await repo.find(
        resource_type=KNOWLEDGE_CREATE_TYPE, resource_slug=KNOWLEDGE_CREATE_SLUG, user_id=user.id
    )
    if existing is not None:
        if existing.status == "approved":
            return existing
        existing.status = "pending"
        existing.decided_at = None
        existing.decided_by = None
        await db.commit()
        await db.refresh(existing)
        return existing
    item = ResourceSubscription(
        resource_type=KNOWLEDGE_CREATE_TYPE,
        resource_slug=KNOWLEDGE_CREATE_SLUG,
        user_id=user.id,
        status="pending",
    )
    item = await repo.save(item)
    await db.commit()
    await db.refresh(item)
    return item


async def has_knowledge_create_grant(db: AsyncSession, user: User) -> bool:
    """个人建库权限是否已获批。"""
    item = await SubscriptionRepository(db).find(
        resource_type=KNOWLEDGE_CREATE_TYPE, resource_slug=KNOWLEDGE_CREATE_SLUG, user_id=user.id
    )
    return item is not None and item.status == "approved"


async def list_mine(db: AsyncSession, user: User) -> list[dict[str, Any]]:
    """当前用户的申请记录，附带资源展示名。"""
    items = await SubscriptionRepository(db).list_mine(user.id)
    await _reconcile_access(db, user, items)
    result = []
    for item in items:
        data = serialize(item)
        if item.resource_type == KNOWLEDGE_CREATE_TYPE:
            data["name"] = KNOWLEDGE_CREATE_LABEL
        else:
            row = await _resource_row(db, item.resource_type, item.resource_slug)
            data["name"] = row.name if row is not None else item.resource_slug
        result.append(data)
    return result


async def list_pending(db: AsyncSession, operator: User) -> list[dict[str, Any]]:
    """审批队列：超管看全部，部门管理员只看本部门范围的资源。"""
    items = await SubscriptionRepository(db).list_pending()
    user_ids = {item.user_id for item in items}
    users = {}
    if user_ids:
        rows = await db.scalars(select(User).where(User.id.in_(user_ids)))
        users = {user.id: user for user in rows}
    result = []
    for item in items:
        if item.resource_type == KNOWLEDGE_CREATE_TYPE:
            name = KNOWLEDGE_CREATE_LABEL
            handleable = operator.role == "superadmin"
        else:
            row = await _resource_row(db, item.resource_type, item.resource_slug)
            name = row.name if row is not None else item.resource_slug
            handleable = _approver_can_handle(row, operator)
        if not handleable:
            continue
        user = users.get(item.user_id)
        data = serialize(item)
        data.update(
            {
                "name": name,
                "resource_missing": row is None,
                "applicant_uid": user.uid if user else "",
                "applicant_name": user.username if user else "",
            }
        )
        result.append(data)
    return result


async def pending_count(db: AsyncSession, operator: User) -> int:
    """导航角标使用的待审批数量，口径与列表一致。"""
    return len(await list_pending(db, operator))


async def decide(db: AsyncSession, subscription_id: int, operator: User, *, approve: bool) -> dict[str, Any]:
    """审批单条申请；已处理的记录不重复改变结果。"""
    repo = SubscriptionRepository(db)
    item = await repo.get(subscription_id, for_update=True)
    if item is None:
        raise SubscriptionError("申请已被申请人撤回或删除", status_code=404)
    if item.status != "pending":
        raise SubscriptionError("该申请已处理", status_code=409)
    if item.user_id == operator.id:
        raise SubscriptionError("不能审批自己的申请", status_code=403)
    if item.resource_type == KNOWLEDGE_CREATE_TYPE:
        if operator.role != "superadmin":
            raise SubscriptionError("只有超级管理员可以审批建库权限", status_code=403)
    else:
        row = await _resource_row(db, item.resource_type, item.resource_slug)
        if not _approver_can_handle(row, operator):
            raise SubscriptionError("只能审批本部门范围的申请", status_code=403)
    item.status = "approved" if approve else "rejected"
    item.decided_at = utc_now()
    item.decided_by = operator.uid
    await db.commit()
    await db.refresh(item)
    return serialize(item)


async def revoke(db: AsyncSession, subscription_id: int, *, operator: User) -> None:
    """撤销订阅，用户下一次请求即失去读取权限；申请人只能撤销自己的记录。"""
    repo = SubscriptionRepository(db)
    item = await repo.get(subscription_id, for_update=True)
    if item is None:
        raise SubscriptionError("申请不存在或已被处理", status_code=404)
    if operator.role not in ADMIN_ROLES and item.user_id != operator.id:
        raise SubscriptionError("无权撤销他人的申请", status_code=403)
    await repo.delete(item)
    await db.commit()


async def approved_slugs(db: AsyncSession, *, user_id: int, resource_type: str) -> set[str]:
    """权限查询入口：某个用户在某类资源上已获批的标识。"""
    return await SubscriptionRepository(db).approved_slugs(user_id=user_id, resource_type=resource_type)
