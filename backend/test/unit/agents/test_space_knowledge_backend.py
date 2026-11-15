"""用仓库里的三个真实空间验证只读前缀视图、挂载与越界拒绝。"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from deepagents.backends.protocol import PERMISSION_DENIED, BackendProtocol, FileInfo, GlobResult, GrepResult, LsResult

from yuxi.infrastructure.minio import ObjectSizeLimitError, StorageError
from yuxi.modules.agents.runtime.sandbox import backend as backend_module
from yuxi.modules.agents.runtime.sandbox import space_knowledge
from yuxi.modules.agents.runtime.sandbox.backend import create_agent_composite_backend
from yuxi.modules.agents.runtime.sandbox.space_knowledge import SpaceKnowledgeBackend

SPACES_DIR = Path(__file__).parents[2] / "data" / "spaces"
SPACES = ("hybrid-bonding", "sbol3-synbio", "cbam-reporting")


class MissingBucket(Exception):
    """模拟 minio `S3Error` 的 code 字段，让"桶不存在"走不记日志的分支。"""

    code = "NoSuchBucket"


class FakeStorage:
    """对象键到文本的最小对象存储替身；缺失对象用真实客户端的异常类型。"""

    def __init__(self, objects: dict[str, bytes], *, missing_bucket: bool = False, unreadable: tuple[str, ...] = ()) -> None:
        self.objects = objects
        self.missing_bucket = missing_bucket
        self.unreadable = set(unreadable)
        self.reads: list[str] = []

    async def alist_object_metadata(self, bucket_name: str, prefix: str) -> list[dict]:
        if self.missing_bucket:
            raise StorageError("桶 'spaces' 不存在") from MissingBucket()
        return [{"object_name": name, "last_modified": None} for name in sorted(self.objects) if name.startswith(prefix)]

    async def adownload_file(self, bucket_name: str, object_name: str, *, max_bytes: int | None = None) -> bytes:
        self.reads.append(object_name)
        if object_name in self.unreadable:
            raise StorageError(f"对象 '{object_name}' 读取失败")
        if object_name not in self.objects:
            raise StorageError(f"对象 '{object_name}' 不存在")
        body = self.objects[object_name]
        if max_bytes is not None and len(body) > max_bytes:
            raise ObjectSizeLimitError(f"对象 '{object_name}' 超过读取上限")
        return body


class StubBackend(BackendProtocol):
    """占位默认后端：只证明路由与根级合并，不涉及沙盒。"""

    def ls(self, path: str) -> LsResult:
        return LsResult(entries=[])

    async def als(self, path: str) -> LsResult:
        return self.ls(path)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        return GlobResult(matches=[FileInfo(path="/home/user/workspace/note.md", is_dir=False)])

    async def agrep(self, pattern: str, path: str | None = None, glob: str | None = None, *, max_count: int | None = None) -> GrepResult:
        return GrepResult(matches=[])


def load_space(slug: str) -> dict[str, bytes]:
    """把夹具目录摊平成对象键，模拟开发者发布到 spaces 桶的结果。"""
    root = SPACES_DIR / slug
    return {f"{slug}/{path.relative_to(root).as_posix()}": path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def storage(monkeypatch) -> FakeStorage:
    fake = FakeStorage({key: value for slug in SPACES for key, value in load_space(slug).items()})
    monkeypatch.setattr(space_knowledge, "get_minio_client", lambda: fake)
    return fake


@pytest.fixture
def mounted(monkeypatch, storage):
    """按真实装配方式建 Composite，默认后端换成不碰沙盒的替身。"""
    monkeypatch.setattr(backend_module, "UserSandboxBackend", lambda **_kwargs: StubBackend())

    def build(slug: str | None):
        context = SimpleNamespace(workdir_relative_path="demo", uid="one", space_slug=slug)
        return create_agent_composite_backend(context)

    return build


async def test_space_tree_lists_markdown_only(storage):
    """发布约定是 Markdown，且对象存储的目录标记键不能变成文件条目。"""
    storage.objects["hybrid-bonding/"] = b""
    storage.objects["hybrid-bonding/sub/"] = b""
    storage.objects["hybrid-bonding/键合工艺DOE.xlsx"] = b"not a markdown"
    storage.objects["hybrid-bonding/材料/"] = b""
    storage.objects["hybrid-bonding/材料/notes.md"] = b"# notes\n"
    backend = SpaceKnowledgeBackend(space_slug="hybrid-bonding")
    entries = {entry["path"]: entry["is_dir"] for entry in (await backend.als("/")).entries}
    assert entries["/index.md"] is False
    assert entries["/sub"] is True
    assert entries["/材料"] is True
    assert "/键合工艺DOE.xlsx" not in entries
    assert "/sub/" not in entries
    # 键等于前缀的目录标记对象不能变成空名或根条目
    assert all(path not in {"", "/"} for path in entries)


async def test_uppercase_suffix_is_listed_and_readable(storage):
    """列表与读取必须对同一组文件成立：后缀大小写不能造成"列不出但读得到"。"""
    storage.objects["cbam-reporting/README.MD"] = b"# upper\n"
    backend = SpaceKnowledgeBackend(space_slug="cbam-reporting")
    assert "/README.MD" in {entry["path"] for entry in (await backend.als("/")).entries}
    assert (await backend.aread("/README.MD")).error is None


async def test_one_unreadable_object_does_not_abort_content_search(storage):
    """检索时单个对象读不动只丢它自己，不能把整次结果变成错误。"""
    storage.unreadable = {"hybrid-bonding/index.md"}
    result = await SpaceKnowledgeBackend(space_slug="hybrid-bonding").agrep("键合")
    assert result.error is None
    paths = {match["path"] for match in result.matches}
    assert paths and "/index.md" not in paths


async def test_oversized_file_is_refused_by_the_read_limit(storage, monkeypatch):
    """单文件上限是真实存在的拒绝理由，不能被当成"文件为空"。"""
    monkeypatch.setattr(space_knowledge, "MAX_FILE_BYTES", 16)
    result = await SpaceKnowledgeBackend(space_slug="hybrid-bonding").aread("/index.md")
    assert result.error


async def test_read_paginates_raw_markdown_without_line_numbers(storage):
    """行号由文件中间件添加；这里返回原文，否则模型会看到两套行号。"""
    raw = (SPACES_DIR / "hybrid-bonding" / "index.md").read_text(encoding="utf-8").splitlines(keepends=True)
    page = await SpaceKnowledgeBackend(space_slug="hybrid-bonding").aread("/index.md", offset=1, limit=2)
    assert page.file_data["content"] == "".join(raw[1:3])
    assert (page.start_line, page.end_line, page.next_offset, page.total_lines) == (2, 3, 3, len(raw))


async def test_read_to_eof_reports_no_further_pages(storage):
    """读完最后一页必须给出 next_offset=None，否则中间件会一直提示"还有后续"。"""
    raw = (SPACES_DIR / "sbol3-synbio" / "index.md").read_text(encoding="utf-8").splitlines(keepends=True)
    page = await SpaceKnowledgeBackend(space_slug="sbol3-synbio").aread("/index.md", offset=0, limit=len(raw) + 5)
    assert page.end_line == len(raw) and page.next_offset is None and page.total_lines == len(raw)


async def test_read_past_end_is_an_error(storage):
    """越过文件末尾由共享分页逻辑报错，不能伪装成"空文件"误导模型。"""
    page = await SpaceKnowledgeBackend(space_slug="sbol3-synbio").aread("/index.md", offset=9999)
    assert page.error and "exceeds file length" in page.error


async def test_zero_limit_reads_no_lines(storage):
    """limit<=0 是模型的退化入参：标记为未检查窗口，而不是"文件为空"。"""
    page = await SpaceKnowledgeBackend(space_slug="hybrid-bonding").aread("/index.md", limit=0)
    assert page.no_lines_requested and page.error is None


async def test_non_markdown_and_missing_objects_are_refused(storage):
    """空间只服务 UTF-8 Markdown：二进制后缀、非 UTF-8 与缺失键都不进模型上下文。"""
    storage.objects["cbam-reporting/乱码.md"] = b"\xff\xfe\x00bad"
    backend = SpaceKnowledgeBackend(space_slug="cbam-reporting")
    assert "Markdown" in (await backend.aread("/a.png")).error
    assert "不存在" in (await backend.aread("/nope.md")).error
    assert "UTF-8" in (await backend.aread("/乱码.md")).error


async def test_writes_are_refused_without_touching_storage(storage):
    """只读挂载：写、编辑、删除与批量上传下载逐条拒绝，且不产生任何对象读取。"""
    backend = SpaceKnowledgeBackend(space_slug="cbam-reporting")
    assert backend.write("/x.md", "boom").error
    assert backend.edit("/x.md", "a", "b").error
    assert backend.delete("/x.md").error
    assert backend.upload_files([("/x.md", b"boom")])[0].error == PERMISSION_DENIED
    assert backend.download_files(["/x.md"])[0].error == PERMISSION_DENIED
    assert (await backend.awrite("/x.md", "boom")).error
    assert (await backend.aedit("/x.md", "a", "b")).error
    assert (await backend.adelete("/x.md")).error
    assert (await backend.aupload_files([("/x.md", b"boom")]))[0].error == PERMISSION_DENIED
    assert (await backend.adownload_files(["/x.md"]))[0].error == PERMISSION_DENIED
    assert storage.reads == []


async def test_sync_calls_never_propagate_errors_into_root_merges(storage):
    """同步路径不访问对象存储，返回空结果而不是异常，避免打断同步合并。"""
    backend = SpaceKnowledgeBackend(space_slug="cbam-reporting")
    assert backend.ls("/").entries == []
    assert backend.glob("**/*.md").matches == []
    assert backend.grep("CBAM").matches == []
    assert backend.read("/index.md").error


async def test_glob_and_content_search_stay_inside_the_space(storage):
    """每个空间只能看到自己的对象键：目录、读取与内容检索都不跨前缀。"""
    backend = SpaceKnowledgeBackend(space_slug="hybrid-bonding")
    assert {match["path"] for match in (await backend.aglob("*.md")).matches} >= {"/index.md", "/bonding-window.md"}
    assert (await backend.aread("/cbam-reporting/index.md")).error
    matches = (await backend.agrep("空洞")).matches
    assert matches and {match["path"] for match in matches} <= {
        "/index.md",
        "/bonding-window.md",
        "/键合工艺DOE.md",
        "/anneal-and-characterization.md",
        "/surface-activation.md",
        "/log.md",
        "/AGENT.md",
    }
    assert (await backend.agrep("CBAM")).matches == []


async def test_glob_handles_recursive_and_anchored_patterns(storage):
    """`**/*.md` 要包含根层文件，`/x.md` 这类锚定模式也不能静默返回空。"""
    backend = SpaceKnowledgeBackend(space_slug="hybrid-bonding")
    assert "/index.md" in {match["path"] for match in (await backend.aglob("**/*.md")).matches}
    assert [match["path"] for match in (await backend.aglob("/index.md")).matches] == ["/index.md"]
    assert (await backend.aglob("*.xlsx")).matches == []


async def test_content_search_honours_glob_and_max_count(storage):
    """检索要能收窄到单个文件并如实报告截断，否则模型会把局部当全量。"""
    backend = SpaceKnowledgeBackend(space_slug="hybrid-bonding")
    scoped = await backend.agrep("键合", glob="bonding-window.md")
    assert scoped.matches and {match["path"] for match in scoped.matches} == {"/bonding-window.md"}
    capped = await backend.agrep("键合", max_count=2)
    assert len(capped.matches) == 2 and capped.truncated


def test_slug_must_be_a_single_segment(storage):
    """前缀即空间边界：带分隔符的 slug 等于放开跨空间读写。"""
    with pytest.raises(ValueError):
        SpaceKnowledgeBackend(space_slug="hybrid-bonding/../cbam-reporting")
    with pytest.raises(ValueError):
        SpaceKnowledgeBackend(space_slug=" ")


async def test_path_traversal_fails_before_touching_storage(storage):
    """对象键靠 slug + 相对路径拼出；跳转段必须在拼键前失败，而不是落到别的空间。"""
    backend = SpaceKnowledgeBackend(space_slug="hybrid-bonding")
    assert (await backend.aread("/../cbam-reporting/index.md")).error
    assert (await backend.aread("/sub\\index.md")).error
    assert (await backend.als("/../")).error
    assert storage.reads == []


async def test_composite_remaps_paths_under_spaces(mounted):
    """挂载点对外是 /spaces/：子后端看到 /，模型看到 /spaces/xxx。"""
    composite = mounted("hybrid-bonding")
    assert "/spaces/" in {entry["path"] for entry in (await composite.als("/")).entries}
    assert "/spaces/index.md" in {entry["path"] for entry in (await composite.als("/spaces")).entries}
    assert (await composite.aread("/spaces/index.md")).error is None
    assert (await composite.aread("/spaces/../spaces/index.md")).error


async def test_agent_without_space_prefix_sees_empty_directory(mounted):
    """没有同名前缀的 Agent 拿到空列表；根级列举不受影响。"""
    composite = mounted("no-such-space")
    assert list(composite.routes) == ["/spaces/"]
    assert (await composite.als("/spaces")).entries == []
    assert "/spaces/" in {entry["path"] for entry in (await composite.als("/")).entries}


async def test_agent_without_space_slug_mounts_nothing(mounted):
    """未绑定空间的装配不注册路由，空间目录根本不存在。"""
    assert mounted(None).routes == {}


async def test_agent_slug_alone_does_not_mount_a_space(monkeypatch):
    """空间前缀只认显式绑定的 space_slug，不再猜 Agent 标识。"""
    monkeypatch.setattr(backend_module, "UserSandboxBackend", lambda **_kwargs: StubBackend())
    context = SimpleNamespace(workdir_relative_path="demo", uid="one", agent_slug="hybrid-bonding", space_slug=None)
    assert create_agent_composite_backend(context).routes == {}


async def test_missing_bucket_keeps_root_glob_and_grep_working(mounted, storage):
    """桶还没建时空间视为空；根级 glob/grep 仍返回工作目录结果，而不是整体失败。"""
    storage.missing_bucket = True
    composite = mounted("hybrid-bonding")
    result = await composite.aglob("**/*.md")
    assert result.error is None
    assert {match["path"] for match in result.matches} == {"/home/user/workspace/note.md"}
    assert (await composite.agrep("键合", path="/")).error is None
    assert (await composite.agrep("键合", path="/spaces")).error is None
