"""显式升级连接器共享范围字段，存量连接器保持全平台可见。"""

import asyncio

from sqlalchemy import text

from yuxi.infrastructure.postgres.manager import PostgresManager
from yuxi.infrastructure.postgres.schema import get_schema_versions
from yuxi.migrations.schema import schema_migration_lock

_GLOBAL_SCOPE = '{"version": 2, "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []}, "manage_scope": null}'


async def upgrade_mcp_share_config(manager: PostgresManager) -> None:
    """在初始化锁内把 business v2 升级到 v3。"""
    async with schema_migration_lock(manager):
        versions = await get_schema_versions(manager)
        if versions.get("business") in (3, 4) and "initializing" not in versions:
            if not await _share_config_column_exists(manager):
                raise RuntimeError("business 版本已升级但 mcp_servers.share_config 缺失，请检查迁移日志")
            return
        if versions.get("business") != 2 or "initializing" in versions:
            raise RuntimeError("连接器共享范围升级需要已完成初始化的 business v2")
        async with manager.async_engine.begin() as conn:
            await conn.execute(text("ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS share_config JSON"))
            await conn.execute(
                text("UPDATE mcp_servers SET share_config = CAST(:scope AS JSON) WHERE share_config IS NULL"),
                {"scope": _GLOBAL_SCOPE},
            )
            await conn.execute(
                text("UPDATE yuxi_schema_migrations SET version = 3, applied_at = CURRENT_TIMESTAMP WHERE domain = 'business'")
            )


async def _share_config_column_exists(manager: PostgresManager) -> bool:
    """早退前确认列真的存在，避免版本号已升而结构缺失被静默放过。"""
    async with manager.async_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = 'mcp_servers' AND column_name = 'share_config'"
            )
        )
        return result.first() is not None


async def main() -> None:
    """使用当前部署连接，显式执行一次结构升级。"""
    manager = PostgresManager()
    manager.initialize()
    try:
        await upgrade_mcp_share_config(manager)
    finally:
        await manager.close()


if __name__ == "__main__":
    asyncio.run(main())
