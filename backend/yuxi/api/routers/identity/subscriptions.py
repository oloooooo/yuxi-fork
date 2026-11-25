"""资源订阅申请与审批路由。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.api.dependencies.auth import get_admin_user, get_db, get_required_user
from yuxi.modules.identity.models import User
from yuxi.modules.identity.subscriptions import service
from yuxi.modules.identity.subscriptions.service import SubscriptionError

router = APIRouter(prefix="/subscriptions", tags=["订阅"])


class SubscriptionRequest(BaseModel):
    """申请订阅的入参。"""

    model_config = ConfigDict(extra="forbid")

    resource_type: str = Field(pattern="^(mcp|agent|skill|knowledge_create)$")
    resource_slug: str = Field(min_length=1, max_length=100)


@router.get("/catalog")
async def list_catalog_route(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """列出可申请资源与当前用户的申请状态。"""
    return {"success": True, "data": await service.list_catalog(db, current_user)}


@router.get("/mine")
async def list_mine_route(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """当前用户的申请记录。"""
    return {"success": True, "data": await service.list_mine(db, current_user)}


@router.post("")
async def submit_request_route(
    payload: SubscriptionRequest,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """提交订阅申请；重复申请返回已有记录。"""
    try:
        item = await service.submit_request(db, current_user, payload.resource_type, payload.resource_slug)
    except SubscriptionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"success": True, "data": service.serialize(item)}


@router.get("/pending")
async def list_pending_route(
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """审批队列：超管看全部，部门管理员只看本部门范围的资源。"""
    items = await service.list_pending(db, current_user)
    return {"success": True, "data": {"items": items, "count": len(items)}}


@router.get("/pending/count")
async def pending_count_route(
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """导航角标使用的待审批数量。"""
    return {"success": True, "data": {"count": await service.pending_count(db, current_user)}}


@router.post("/{subscription_id}/approve")
async def approve_route(
    subscription_id: int,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """批准订阅。"""
    try:
        item = await service.decide(db, subscription_id, current_user, approve=True)
    except SubscriptionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"success": True, "data": item}


@router.post("/{subscription_id}/reject")
async def reject_route(
    subscription_id: int,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """驳回订阅。"""
    try:
        item = await service.decide(db, subscription_id, current_user, approve=False)
    except SubscriptionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"success": True, "data": item}


@router.delete("/{subscription_id}")
async def revoke_route(
    subscription_id: int,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """撤销订阅：申请人可撤销自己的，超级管理员可撤销任意一条。"""
    try:
        await service.revoke(db, subscription_id, operator=current_user)
    except SubscriptionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"success": True}
