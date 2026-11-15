"""业务空间内容的发布与列举：内容写在 Agent 只读挂载的对象存储前缀下。

空间标识与 Agent slug 相同，发布路径 `spaces/{slug}/{相对路径}` 就是运行时
`/spaces/{相对路径}`；本模块只负责写入那一侧，读取由 space_knowledge 负责。
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from yuxi.infrastructure.minio import StorageError, get_minio_client
from yuxi.modules.agents.runtime.sandbox.space_knowledge import SPACES_BUCKET

# 与 `AgentRepository._slugify` 同形：允许下划线，长度上限 56，让任何既有 Agent 都能发布内容。
_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,55}$")


def normalize_slug(slug: str) -> str:
    """校验空间标识：与 Agent slug 同形，只允许小写字母、数字、下划线和连字符。"""
    value = (slug or "").strip()
    if not _SLUG_PATTERN.fullmatch(value):
        raise ValueError("空间标识只能用小写字母、数字、下划线和连字符，并以字母或数字开头")
    return value


def normalize_relative_path(path: str) -> str:
    """校验发布路径：只接受空间内的相对 Markdown 路径。"""
    raw = (path or "").replace("\\", "/").strip()
    if not raw or raw.startswith("/"):
        raise ValueError(f"路径 {path!r} 必须是空间内的相对路径")
    parts = PurePosixPath(raw).parts
    if ".." in parts:
        raise ValueError(f"路径 {path!r} 不能包含 ..")
    if not raw.lower().endswith(".md"):
        raise ValueError(f"路径 {path!r} 不是 Markdown；空间只发布 .md 文件")
    return "/".join(parts)


async def list_spaces() -> list[dict[str, Any]]:
    """列出已发布的空间标识与各自的 Markdown 数量。"""
    try:
        objects = await get_minio_client().alist_object_metadata(SPACES_BUCKET, "")
    except StorageError:
        return []
    counts: dict[str, int] = {}
    for item in objects:
        name = str(item.get("object_name", ""))
        slug, _, relative = name.partition("/")
        if _SLUG_PATTERN.fullmatch(slug) and relative.lower().endswith(".md"):
            counts[slug] = counts.get(slug, 0) + 1
    return [{"slug": slug, "files": count} for slug, count in sorted(counts.items())]


async def list_space_files(slug: str) -> list[str]:
    """列出一个空间已发布的 Markdown 相对路径；桶尚不存在时视为空空间。"""
    space = normalize_slug(slug)
    try:
        objects = await get_minio_client().alist_object_metadata(SPACES_BUCKET, f"{space}/")
    except StorageError:
        return []
    prefix_length = len(space) + 1
    paths = [
        str(item.get("object_name", ""))[prefix_length:] for item in objects if str(item.get("object_name", "")).lower().endswith(".md")
    ]
    return sorted(paths)


async def publish_files(slug: str, files: list[tuple[str, bytes]]) -> dict[str, Any]:
    """把一批 Markdown 写入空间前缀；任一路径非法则整批拒绝。"""
    space = normalize_slug(slug)
    if not files:
        raise ValueError("没有可发布的文件")
    prepared = [(normalize_relative_path(path), data) for path, data in files]
    client = get_minio_client()
    for relative, data in prepared:
        await client.aupload_file(SPACES_BUCKET, f"{space}/{relative}", data)
    return {"slug": space, "saved": [relative for relative, _ in prepared]}


async def delete_space(slug: str) -> int:
    """删除一个空间前缀下的全部对象，返回删除数量。"""
    return await get_minio_client().adelete_objects_by_prefix(SPACES_BUCKET, f"{normalize_slug(slug)}/")
