"""资源订阅申请的持久化查询。"""

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.modules.identity.subscriptions.models import ResourceSubscription


class SubscriptionRepository:
    """订阅表读写，只处理持久化，不判断资源是否存在。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def find(self, *, resource_type: str, resource_slug: str, user_id: int) -> ResourceSubscription | None:
        """按（类型、标识、申请人）读取唯一记录。"""
        result = await self.db.execute(
            select(ResourceSubscription).where(
                ResourceSubscription.resource_type == resource_type,
                ResourceSubscription.resource_slug == resource_slug,
                ResourceSubscription.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get(self, subscription_id: int) -> ResourceSubscription | None:
        """按主键读取，可跨用户（审批场景）。"""
        return await self.db.get(ResourceSubscription, subscription_id)

    async def list_mine(self, user_id: int) -> list[ResourceSubscription]:
        """申请人自己的全部记录，按时间倒序。"""
        result = await self.db.execute(
            select(ResourceSubscription)
            .where(ResourceSubscription.user_id == user_id)
            .order_by(ResourceSubscription.id.desc())
        )
        return list(result.scalars().all())

    async def list_pending(self, *, limit: int = 200) -> list[ResourceSubscription]:
        """待审批队列，最早申请的先处理。"""
        result = await self.db.execute(
            select(ResourceSubscription)
            .where(ResourceSubscription.status == "pending")
            .order_by(ResourceSubscription.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def pending_count(self) -> int:
        """待审批数量，用于导航角标。"""
        return int(
            await self.db.scalar(
                select(func.count()).select_from(ResourceSubscription).where(ResourceSubscription.status == "pending")
            )
            or 0
        )

    async def approved_slugs(self, *, user_id: int, resource_type: str) -> set[str]:
        """当前用户已获批的资源标识集合。"""
        result = await self.db.execute(
            select(ResourceSubscription.resource_slug).where(
                ResourceSubscription.user_id == user_id,
                ResourceSubscription.resource_type == resource_type,
                ResourceSubscription.status == "approved",
            )
        )
        return set(result.scalars().all())

    async def save(self, row: ResourceSubscription) -> ResourceSubscription:
        """新增或更新单条记录，不提交事务。"""
        self.db.add(row)
        await self.db.flush()
        return row

    async def delete(self, row: ResourceSubscription) -> None:
        """删除单条记录，不提交事务。"""
        await self.db.delete(row)

    async def delete_user_grants(self, user_id: int) -> None:
        """用户被清理时一并撤销其订阅。"""
        await self.db.execute(delete(ResourceSubscription).where(ResourceSubscription.user_id == user_id))
