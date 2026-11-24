"""新增资源订阅申请表，business v4 升级到 v5。"""

import asyncio

from sqlalchemy import text

from yuxi.infrastructure.postgres.manager import PostgresManager
from yuxi.infrastructure.postgres.schema import get_schema_versions
from yuxi.migrations.schema import schema_migration_lock


async def _table_exists(manager: PostgresManager) -> bool:
    """检查当前 schema 里是否已有订阅表。"""
    async with manager.async_engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = 'resource_subscriptions'"
            )
        )
        return result.first() is not None


async def upgrade_resource_subscriptions(manager: PostgresManager) -> None:
    """在初始化锁内把 business v4 升级到 v5。"""
    async with schema_migration_lock(manager):
        versions = await get_schema_versions(manager)
        if versions.get("business") == 5 and "initializing" not in versions:
            if not await _table_exists(manager):
                raise RuntimeError("business 版本为 5 但 resource_subscriptions 缺失，请检查迁移日志")
            return
        if versions.get("business") != 4 or "initializing" in versions:
            raise RuntimeError("资源订阅升级需要已完成初始化的 business v4")
        async with manager.async_engine.begin() as conn:
            await conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS resource_subscriptions ("
                    "id SERIAL PRIMARY KEY, "
                    "resource_type VARCHAR(20) NOT NULL, "
                    "resource_slug VARCHAR(100) NOT NULL, "
                    "user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
                    "status VARCHAR(20) NOT NULL DEFAULT 'pending', "
                    "created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                    "decided_at TIMESTAMPTZ NULL, "
                    "decided_by VARCHAR(100) NULL, "
                    "CONSTRAINT uq_resource_subscriptions_target "
                    "UNIQUE (resource_type, resource_slug, user_id)"
                    ")"
                )
            )
            await conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_resource_subscriptions_user_id ON resource_subscriptions (user_id)")
            )
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_resource_subscriptions_status "
                    "ON resource_subscriptions (resource_type, status)"
                )
            )
            await conn.execute(
                text("UPDATE yuxi_schema_migrations SET version = 5, applied_at = CURRENT_TIMESTAMP WHERE domain = 'business'")
            )


async def main() -> None:
    """命令行入口：python -m yuxi.migrations.resource_subscriptions。"""
    manager = PostgresManager()
    await manager.initialize()
    try:
        await upgrade_resource_subscriptions(manager)
        print("resource_subscriptions 迁移完成")
    finally:
        # 一次性脚本只释放 SQLAlchemy 引擎；LangGraph psycopg 池在空库连接下关闭会挂起。
        if manager.async_engine is not None:
            await manager.async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
