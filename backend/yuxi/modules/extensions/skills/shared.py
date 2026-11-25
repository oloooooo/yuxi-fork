"""共享 Skill 的安装确认、治理与删除用例。"""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.infrastructure.runtime_settings import get_skill_data_dir
from yuxi.modules.extensions.mcp.service import get_enabled_mcp_server_slugs
from yuxi.modules.extensions.skills.access import (
    MissingMcpDependencies as MissingMcpDependencies,
)
from yuxi.modules.extensions.skills.access import (
    can_skill_depend_on as can_skill_depend_on,
)
from yuxi.modules.extensions.skills.access import (
    get_allowed_skill_access_levels as get_allowed_skill_access_levels,
)
from yuxi.modules.extensions.skills.access import (
    get_manageable_skill_or_raise as get_manageable_skill_or_raise,
)
from yuxi.modules.extensions.skills.access import (
    get_management_readable_skill_or_raise as get_management_readable_skill_or_raise,
)
from yuxi.modules.extensions.skills.access import (
    get_skill_or_raise as get_skill_or_raise,
)
from yuxi.modules.extensions.skills.access import (
    get_skills_root_dir as get_skills_root_dir,
)
from yuxi.modules.extensions.skills.access import (
    is_builtin_skill as is_builtin_skill,
)
from yuxi.modules.extensions.skills.access import (
    lock_accessible_shared_skill_for_file as lock_accessible_shared_skill_for_file,
)
from yuxi.modules.extensions.skills.access import (
    lock_accessible_shared_skills_for_runtime as lock_accessible_shared_skills_for_runtime,
)
from yuxi.modules.extensions.skills.access import (
    normalize_skill_share_config as normalize_skill_share_config,
)
from yuxi.modules.extensions.skills.access import (
    reject_bound_skill_policy_change as reject_bound_skill_policy_change,
)
from yuxi.modules.extensions.skills.access import (
    resolved_shared_skill as resolved_shared_skill,
)
from yuxi.modules.extensions.skills.access import (
    user_can_access_skill as user_can_access_skill,
)
from yuxi.modules.extensions.skills.access import (
    user_can_manage_skill as user_can_manage_skill,
)
from yuxi.modules.extensions.skills.access import (
    validate_skill_dependencies as validate_skill_dependencies,
)
from yuxi.modules.extensions.skills.builtin.sync import (
    init_builtin_skills as init_builtin_skills,
)
from yuxi.modules.extensions.skills.builtin.sync import (
    list_builtin_skill_specs as list_builtin_skill_specs,
)
from yuxi.modules.extensions.skills.content import commit_skill_content
from yuxi.modules.extensions.skills.install_draft import consume_installed_draft_items, load_and_select_draft_items
from yuxi.modules.extensions.skills.models import Skill
from yuxi.modules.extensions.skills.projection import (
    commit_skill_policy_and_refresh_projections,
    invalidate_skill_projections,
)
from yuxi.modules.extensions.skills.repository import SkillRepository
from yuxi.modules.identity.models import User
from yuxi.modules.identity.permissions.resource_permission import ADMIN_ROLES, stamp_owner_department
from yuxi.modules.identity.repositories.resource_grants import validate_shared_grants

logger = logging.getLogger(__name__)


async def get_skill_dependency_options(db: AsyncSession, user: User, slug: str | None = None) -> dict[str, list[str] | list[dict]]:
    """返回当前 Skill 可选择的依赖项。"""
    from yuxi.modules.extensions.tools.catalog import get_tool_metadata

    skill_items, tool_list, mcp_names = await asyncio.gather(
        SkillRepository(db).list_enabled_readable(user),
        asyncio.to_thread(get_tool_metadata),
        get_enabled_mcp_server_slugs(db=db),
    )
    skill_slugs = [item.slug for item in skill_items if isinstance(item.slug, str) and item.slug != slug]

    return {
        "tools": [{"slug": tool["slug"], "name": tool.get("name", tool["slug"])} for tool in tool_list],
        "mcps": mcp_names,
        "skills": skill_slugs,
    }


async def confirm_skill_install_draft(
    db: AsyncSession,
    *,
    draft_id: str,
    share_config: dict | None,
    slugs: list[str] | None = None,
    created_mcp_slugs: list[str] | None = None,
    operator: User,
) -> list[dict[str, Any]]:
    """按共享授权确认并发布 Skill 草稿。"""
    if operator.role not in ADMIN_ROLES or operator.is_deleted:
        raise ValueError("共享 Skill 建设需要管理员权限")
    draft_dir, data, draft_items = load_and_select_draft_items(draft_id, slugs, operator)
    source_type = data["source_type"]

    normalized_share_config = normalize_skill_share_config(
        share_config,
        operator_uid=operator.uid,
        source_type=source_type,
        allowed_access_levels=set(get_allowed_skill_access_levels(operator)),
    )
    normalized_share_config = stamp_owner_department(normalized_share_config, operator)

    repo = SkillRepository(db)

    results: list[dict[str, Any]] = []
    installed: list[Skill] = []
    for draft_item in draft_items:
        slug = draft_item.slug
        final_slug = await _generate_available_slug(repo, slug)
        try:
            normalized_share_config = await validate_shared_grants(db, normalized_share_config)
            item = Skill(
                slug=final_slug,
                source_type=source_type,
                share_config=normalized_share_config,
                enabled=True,
                created_by=operator.uid,
            )
            await commit_skill_content(
                db,
                item=item,
                operator=operator,
                expected_revision=None,
                source=draft_item.source_dir,
                source_slug=slug,
            )
            results.append({"slug": item.slug, "requested_slug": slug, "success": True, "skill": item.to_dict()})
            installed.append(item)
        except Exception as exc:
            await db.rollback()
            result = {"slug": slug, "success": False, "error": str(exc)}
            if isinstance(exc, MissingMcpDependencies):
                result["missing_mcps"] = exc.slugs
            results.append(result)

    if installed:
        from yuxi.modules.extensions.mcp.service import (
            mark_skill_owned_mcp_servers,
            sync_mcp_share_config_for_skill,
        )

        created = set(created_mcp_slugs or [])
        # 只取依赖与回传的交集，避免客户端携带无关标识放大锁定顺序。
        created &= {slug for item in installed for slug in (item.mcp_dependencies or [])}
        try:
            for item in installed:
                await mark_skill_owned_mcp_servers(
                    db,
                    slugs=list(item.mcp_dependencies or []),
                    claimed_slugs=created,
                    skill_slug=item.slug,
                    operator=operator,
                )
                await sync_mcp_share_config_for_skill(
                    db,
                    slugs=list(item.mcp_dependencies or []),
                    skill_slug=item.slug,
                    share_config=item.share_config,
                    operator=operator,
                )
            await db.commit()
        except Exception:
            # 归属标记失败只降级为「公有」，不影响已安装结果与草稿消费。
            await db.rollback()
            logger.exception("Failed to mark skill-owned MCP servers")

    consume_installed_draft_items(draft_dir, data, {item["requested_slug"] for item in results if item["success"]})
    return results


async def delete_skill(db: AsyncSession, *, slug: str, operator: User) -> None:
    """删除一个可管理的共享 Skill。"""
    repo = SkillRepository(db)
    item = await repo.get_by_slug(slug, for_update=True)
    if not item:
        raise ValueError(f"技能 '{slug}' 不存在")
    if not user_can_manage_skill(operator, item):
        raise ValueError(f"技能 '{slug}' 不存在或无权管理")
    _ensure_non_builtin(item)
    reject_bound_skill_policy_change(item)

    packages = get_skill_data_dir() / "packages" / slug
    owned = list(packages.iterdir()) if packages.is_dir() else []
    old_shared = get_skills_root_dir() / slug
    if old_shared.exists():
        owned.append(old_shared)
    await invalidate_skill_projections(db, slug)
    from yuxi.modules.extensions.mcp.service import release_skill_owned_mcp_servers

    await release_skill_owned_mcp_servers(
        db, mcp_slugs=list(item.mcp_dependencies or []), skill_slug=slug, skill_id=item.id
    )
    await repo.delete(item)
    await db.commit()
    # 只删除提交前已定位的内容目录，后续同 slug 安装的新目录不属于本次删除。
    for root in owned:
        try:
            await asyncio.to_thread(shutil.rmtree, root)
        except FileNotFoundError:
            pass  # 后续同 slug 提交可能已经清理无引用的旧包。


async def delete_skills_batch(db: AsyncSession, *, slugs: list[str], operator: User) -> list[dict]:
    """批量删除多个 skills（单技能独立的子事务与回滚）。"""
    if len(slugs) > 50:
        raise ValueError("批量删除的技能数量不能超过 50 个")
    results = []
    for slug in slugs:
        try:
            await delete_skill(db, slug=slug, operator=operator)
            results.append({"slug": slug, "success": True})
        except Exception as e:
            if hasattr(db, "rollback"):
                await db.rollback()
            results.append({"slug": slug, "success": False, "error": str(e)})
    return results


async def update_skill_share_config(
    db: AsyncSession,
    *,
    slug: str,
    share_config: dict | None,
    operator: User,
) -> Skill:
    """更新共享 Skill 授权并刷新已有投影。"""
    item = await get_manageable_skill_or_raise(db, operator, slug)
    _ensure_non_builtin(item)
    reject_bound_skill_policy_change(item)
    normalized = normalize_skill_share_config(
        share_config,
        operator_uid=operator.uid,
        source_type=item.source_type,
        allowed_access_levels=set(get_allowed_skill_access_levels(operator)),
    )
    normalized = stamp_owner_department(normalized, operator, previous=item.share_config)
    repo = SkillRepository(db)
    normalized = await validate_shared_grants(db, normalized)
    updated = await repo.update_share_config(item, share_config=normalized, updated_by=operator.uid)
    from yuxi.modules.extensions.mcp.service import sync_mcp_share_config_for_skill

    await sync_mcp_share_config_for_skill(
        db,
        slugs=list(item.mcp_dependencies or []),
        skill_slug=slug,
        share_config=normalized,
        operator=operator,
    )
    await commit_skill_policy_and_refresh_projections(db, slug)
    return updated


async def update_skill_enabled(db: AsyncSession, *, slug: str, enabled: bool, operator: User) -> Skill:
    """更新共享 Skill 启用状态并刷新已有投影。"""
    item = await get_manageable_skill_or_raise(db, operator, slug)
    reject_bound_skill_policy_change(item)
    repo = SkillRepository(db)
    updated = await repo.update_enabled(item, enabled=enabled, updated_by=operator.uid)
    await commit_skill_policy_and_refresh_projections(db, slug)
    return updated


def _ensure_non_builtin(item: Skill) -> None:
    """拒绝对内置 Skill 执行来源文件修改。"""
    if is_builtin_skill(item):
        raise ValueError("内置 skill 不允许执行该操作")


async def _generate_available_slug(repo: SkillRepository, base_slug: str) -> str:
    """为共享来源分配未占用的 slug。"""
    root = get_skills_root_dir()
    if not await repo.exists_slug(base_slug) and not (root / base_slug).exists():
        return base_slug

    idx = 2
    while True:
        candidate = f"{base_slug}-v{idx}"
        if not await repo.exists_slug(candidate) and not (root / candidate).exists():
            return candidate
        idx += 1
