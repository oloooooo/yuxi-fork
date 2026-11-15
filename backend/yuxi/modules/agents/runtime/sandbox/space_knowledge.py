"""把对象存储上的一个空间前缀暴露成只读文件树，供 Agent 按路径浏览与读取。"""

from __future__ import annotations

from pathlib import PurePosixPath

from deepagents.backends.protocol import (
    PERMISSION_DENIED,
    BackendProtocol,
    DeleteResult,
    EditResult,
    FileData,
    FileDownloadResponse,
    FileInfo,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    WriteResult,
)
from deepagents.backends.utils import compile_recursive_glob, grep_matches_from_files, slice_read_response

from yuxi.infrastructure.minio import StorageError, get_minio_client
from yuxi.infrastructure.observability.logging import logger
from yuxi.modules.workspace.runtime_files import MAX_FILE_BYTES

# 空间知识全部落在专用桶的同名一级前缀下，前缀就是空间标识。
SPACES_BUCKET = "spaces"
_READ_ONLY = "空间知识是只读挂载：写入、编辑、删除与上传一律拒绝。"
_TEXT_ONLY = "空间知识只提供 UTF-8 Markdown 文件；其它格式请先转成 .md 再发布。"
_NOT_UTF8 = "该 Markdown 不是 UTF-8 文本，无法作为空间知识读取。"
_SYNC_UNAVAILABLE = "空间知识只通过异步文件工具读取。"


class SpaceKnowledgeBackend(BackendProtocol):
    """单个空间的只读前缀视图；路径以挂载根为界，越界与写操作都不触达对象存储。"""

    def __init__(self, *, space_slug: str) -> None:
        """前缀必须是单个路径段，否则它就是跨空间读写的入口。"""
        slug = str(space_slug or "").strip()
        if not slug or "/" in slug or "\\" in slug or slug in {".", ".."}:
            raise ValueError("space_slug must be a single path segment")
        self._slug = slug

    @staticmethod
    def _relative(path: str) -> str:
        """挂载内路径规范化：拒绝跳转与反斜杠，统一成以 / 开头。"""
        raw = str(path or "/").strip() or "/"
        if raw.startswith("//") or "\\" in raw or ".." in PurePosixPath(raw).parts or not PurePosixPath(raw).is_absolute():
            raise ValueError("invalid space path")
        return PurePosixPath(raw).as_posix()

    def _prefix(self, relative: str) -> str:
        """目录的相对路径映射成对象键前缀。"""
        return f"{self._slug}{relative}".rstrip("/") + "/"

    async def _objects(self) -> list[str]:
        """整个空间一次列举；桶或前缀还不存在等于空空间，不是错误。"""
        try:
            items = await get_minio_client().alist_object_metadata(SPACES_BUCKET, f"{self._slug}/")
        except StorageError as exc:
            # 桶不存在等于空空间；其余对象存储故障同样降级为空，但要留可查的记录。
            if getattr(exc.__cause__, "code", None) != "NoSuchBucket":
                logger.warning("列举空间前缀失败，本次按空空间处理：slug={} error={}", self._slug, exc)
            return []
        return [str(item["object_name"]) for item in items]

    @staticmethod
    def _relative_child(scope_root: str, name: str, prefix: str) -> str:
        """对象键去掉空间前缀，再补上挂载内的相对目录。"""
        child = f"{scope_root}/{name[len(prefix) :]}"
        return child

    @staticmethod
    def _is_served(child: str) -> bool:
        """只服务 Markdown（后缀大小写不敏感）；目录标记键以 / 结尾，一律跳过。"""
        return child.lower().endswith(".md")

    async def als(self, path: str) -> LsResult:
        """列出目录的下一层；空空间或缺失的桶返回空列表，避免打挂根级合并。"""
        try:
            relative = self._relative(path)
        except ValueError as exc:
            return LsResult(error=str(exc))
        prefix = self._prefix(relative)
        scope_root = relative.rstrip("/")
        entries: dict[str, bool] = {}
        for name in await self._objects():
            if not name.startswith(prefix):
                continue
            head, separator, _ = name[len(prefix) :].partition("/")
            if not head:
                continue  # 目录标记对象（键等于前缀或以 / 结尾）不是条目
            child = self._relative_child(scope_root, name, prefix).rstrip("/")
            if separator:
                entries.setdefault(child, True)
            elif self._is_served(child):
                entries.setdefault(child, False)
        return LsResult(entries=[FileInfo(path=child, is_dir=is_dir) for child, is_dir in sorted(entries.items())])

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """按行分页返回原文；行号由文件中间件统一添加，这里不重复编。"""
        try:
            relative = self._relative(file_path)
            if PurePosixPath(relative).suffix.lower() != ".md":
                return ReadResult(error=_TEXT_ONLY)
            content = await get_minio_client().adownload_file(
                SPACES_BUCKET,
                f"{self._slug}{relative}",
                max_bytes=MAX_FILE_BYTES,
            )
        except (StorageError, ValueError) as exc:
            return ReadResult(error=str(exc))
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return ReadResult(error=_NOT_UTF8)
        return slice_read_response(FileData(content=text, encoding="utf-8"), offset, limit)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        """按文件名匹配空间内路径；匹配语义与其它后端共用 deepagents 的实现。"""
        try:
            scope_root = self._relative("/" if path is None else path).rstrip("/")
            matcher = compile_recursive_glob(pattern)
        except ValueError as exc:
            return GlobResult(error=str(exc))
        prefix = self._prefix(scope_root)
        matches: list[FileInfo] = []
        for name in await self._objects():
            if not name.startswith(prefix):
                continue
            child = self._relative_child(scope_root, name, prefix)
            if self._is_served(child) and matcher(child[len(scope_root) :].lstrip("/")):
                matches.append(FileInfo(path=child, is_dir=False))
        return GlobResult(matches=matches)

    async def agrep(self, pattern: str, path: str | None = None, glob: str | None = None, *, max_count: int | None = None) -> GrepResult:
        """在空间内的 Markdown 正文里做字面匹配，语义与其它后端一致由 deepagents 的匹配器实现。

        shortcut: 一次载入范围内的全部 Markdown 再匹配；空间涨到上千文件时应改为发布期前缀索引。
        """
        try:
            scope_root = self._relative("/" if path is None else path).rstrip("/")
        except ValueError as exc:
            return GrepResult(error=str(exc))
        prefix = self._prefix(scope_root)
        files: dict[str, FileData] = {}
        for name in await self._objects():
            if not name.startswith(prefix):
                continue
            child = self._relative_child(scope_root, name, prefix)
            if not self._is_served(child):
                continue
            try:
                content = await get_minio_client().adownload_file(SPACES_BUCKET, name, max_bytes=MAX_FILE_BYTES)
                files[child] = {"content": content.decode("utf-8")}
            except (StorageError, UnicodeDecodeError):
                continue  # 单个文件读不动不打断整次检索，与其余文件的结果一起返回
        return grep_matches_from_files(files, pattern, glob=glob, max_count=max_count)

    def write(self, file_path: str, content: str) -> WriteResult:
        """只读挂载：写入返回权限错误，不抛异常打断整轮工具调用。"""
        return WriteResult(error=_READ_ONLY)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """异步写入同样拒绝。"""
        return self.write(file_path, content)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        """只读挂载：编辑拒绝。"""
        return EditResult(error=_READ_ONLY)

    async def aedit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        """异步编辑同样拒绝。"""
        return self.edit(file_path, old_string, new_string, replace_all)

    def delete(self, file_path: str) -> DeleteResult:
        """只读挂载：删除拒绝。"""
        return DeleteResult(error=_READ_ONLY)

    async def adelete(self, file_path: str) -> DeleteResult:
        """异步删除同样拒绝。"""
        return self.delete(file_path)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """只读挂载：批量上传逐条拒绝。"""
        return [FileUploadResponse(path=path, error=PERMISSION_DENIED) for path, _ in files]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """只读挂载不提供批量下载，读取走 aread。"""
        return [FileDownloadResponse(path=path, error=PERMISSION_DENIED) for path in paths]

    def ls(self, path: str) -> LsResult:
        """同步调用不能等对象存储；返回空列表让同步合并路径不因路由报错。"""
        return LsResult(entries=[])

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """同步读取不可用，明确报错而不是返回空正文。"""
        return ReadResult(error=_SYNC_UNAVAILABLE)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """同步 glob 返回空匹配，避免根级合并因错误提前返回。"""
        return GlobResult(matches=[])

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None, *, max_count: int | None = None) -> GrepResult:
        """同步调用不能阻塞等对象存储，内容检索统一走异步路径。"""
        return GrepResult(matches=[])
