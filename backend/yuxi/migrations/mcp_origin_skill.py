"""显式升级连接器来源字段，存量连接器保持人工/公有。"""

import asyncio

from sqlalchemy import text

from yuxi.infrastructure.postgres.manager import PostgresManager
from yuxi.infrastructure.postgres.schema import get_schema_versions
from yuxi.migrations.schema import schema_migration_lock


async def upgrade_mcp_origin_skill(manager: PostgresManager) -> None:
    """在初始化锁内把 business v3 升级到 v4。"""
    async with schema_migration_lock(manager):
        versions = await get_schema_versions(manager)
        if versions.get("business") == 4 and "initializing" not in versions:
            async with manager.async_engine.connect() as conn:
                column = await conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = 'mcp_servers' AND column_name = 'origin_skill'"
                    )
                )
                if column.first() is None:
                    raise RuntimeError("business 版本为 4 但 mcp_servers.origin_skill 缺失，请检查迁移日志")
            return
        if versions.get("business") != 3 or "initializing" in versions:
            raise RuntimeError("连接器来源升级需要已完成初始化的 business v3")
        async with manager.async_engine.begin() as conn:
            await conn.execute(text("ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS origin_skill VARCHAR(100)"))
            await conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_mcp_servers_origin_skill ON mcp_servers (origin_skill)")
            )
            await conn.execute(
                text("UPDATE yuxi_schema_migrations SET version = 4, applied_at = CURRENT_TIMESTAMP WHERE domain = 'business'")
            )


async def main() -> None:
    """使用当前部署连接，显式执行一次结构升级。"""
    manager = PostgresManager()
    manager.initialize()
    try:
        await upgrade_mcp_origin_skill(manager)
    finally:
        await manager.close()


if __name__ == "__main__":
    asyncio.run(main())
