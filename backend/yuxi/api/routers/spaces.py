"""业务空间内容的发布入口：只向管理员开放，写入 Agent 只读挂载的前缀。"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from yuxi.api.dependencies.auth import get_admin_user
from yuxi.api.uploads import read_upload_with_limit
from yuxi.modules.agents.services import spaces as space_service

_MAX_PUBLISH_BYTES = 10 * 1024 * 1024

spaces = APIRouter(prefix="/spaces", tags=["spaces"], dependencies=[Depends(get_admin_user)])


@spaces.get("")
async def list_spaces():
    """发布页的初始清单：空间标识与文件数。"""
    return {"spaces": await space_service.list_spaces()}


@spaces.get("/{slug}")
async def list_space_files(slug: str):
    """读取一个空间已发布的相对路径。"""
    try:
        paths = await space_service.list_space_files(slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"slug": slug, "files": paths}


@spaces.post("/{slug}/files")
async def publish_files(slug: str, paths: list[str] = Form(...), files: list[UploadFile] = File(...)):
    """发布一批 Markdown；浏览器按目录拖入时用 paths 传相对路径。"""
    if len(paths) != len(files):
        raise HTTPException(status_code=400, detail="路径与文件数量不一致")
    payload: list[tuple[str, bytes]] = []
    for path, upload in zip(paths, files, strict=True):
        try:
            data = await read_upload_with_limit(
                upload,
                max_size_bytes=_MAX_PUBLISH_BYTES,
                too_large_message=f"{path} 超过 10MB",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        payload.append((path, data))
    try:
        return await space_service.publish_files(slug, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@spaces.delete("/{slug}")
async def delete_space(slug: str):
    """删除整个空间前缀；Agent 下次读取时看到空目录。"""
    try:
        deleted = await space_service.delete_space(slug)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"slug": slug, "deleted": deleted}
