"""资源订阅申请映射。"""

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from yuxi.infrastructure.postgres.base import BusinessBase as Base


class ResourceSubscription(Base):
    """用户对连接器、智能体或共享 Skill 的订阅申请与授权。"""

    __tablename__ = "resource_subscriptions"
    __table_args__ = (
        UniqueConstraint("resource_type", "resource_slug", "user_id", name="uq_resource_subscriptions_target"),
        Index("ix_resource_subscriptions_status", "resource_type", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    resource_type = Column(String(20), nullable=False, comment="资源类型：mcp/agent/skill")
    resource_slug = Column(String(100), nullable=False, comment="资源稳定标识")
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="申请人",
    )
    status = Column(String(20), nullable=False, default="pending", comment="pending/approved/rejected")
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    decided_at = Column(DateTime(timezone=True), nullable=True)
    decided_by = Column(String(100), nullable=True, comment="审批人 uid")
