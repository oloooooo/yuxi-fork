"""空间发布侧校验：非法路径与非法标识必须在写入对象存储前被拒绝。"""

from __future__ import annotations

import pytest

from yuxi.infrastructure.minio import StorageError
from yuxi.modules.agents.services import spaces


class FakeStorage:
    """记录写入的假对象存储，验证拒绝路径不会碰存储。"""

    def __init__(self, objects: dict[str, bytes] | None = None):
        self.objects = dict(objects or {})
        self.writes: list[str] = []

    async def alist_object_metadata(self, bucket_name: str, prefix: str) -> list[dict]:
        return [{"object_name": name} for name in sorted(self.objects) if name.startswith(prefix)]

    async def aupload_file(self, bucket_name: str, name: str, data: bytes, **_: object):
        self.writes.append(name)
        self.objects[name] = data

    async def adelete_objects_by_prefix(self, bucket_name: str, prefix: str) -> int:
        removed = [name for name in self.objects if name.startswith(prefix)]
        for name in removed:
            del self.objects[name]
        return len(removed)


@pytest.fixture
def storage(monkeypatch):
    fake = FakeStorage({"hybrid-bonding/index.md": b"# index\n", "old-space/a.md": b"# a\n"})
    monkeypatch.setattr(spaces, "get_minio_client", lambda: fake)
    return fake


def test_slug_matches_agent_slug_shape():
    """空间标识必须能直接当对象前缀用：与 `_slugify` 同形（允许下划线），拒绝大写与分隔符。"""
    assert spaces.normalize_slug(" hybrid-bonding ") == "hybrid-bonding"
    assert spaces.normalize_slug("sales_2026") == "sales_2026"
    for bad in ["", "Hybrid", "a b", "a/b", "../x", "-lead", "a" * 57]:
        with pytest.raises(ValueError):
            spaces.normalize_slug(bad)


def test_publish_path_must_be_relative_markdown():
    """越界路径与非 Markdown 一律在写入前拒绝。"""
    assert spaces.normalize_relative_path("sub\\a.md") == "sub/a.md"
    for bad in ["../other/a.md", "/etc/a.md", "a.txt", "", "sub/../../a.md"]:
        with pytest.raises(ValueError):
            spaces.normalize_relative_path(bad)


async def test_listing_a_space_before_the_bucket_exists_is_empty(storage):
    """首次发布前（桶还没建）列举返回空，而不是让页面 500。"""

    def boom(*_args, **_kwargs):
        raise StorageError("对象 'spaces/new-space/' 不存在")

    storage.alist_object_metadata = boom
    assert await spaces.list_space_files("new-space") == []


async def test_publish_rejects_the_whole_batch_when_one_path_is_illegal(storage):
    """一条路径非法时整批不写，避免半个空间被发布。"""
    with pytest.raises(ValueError):
        await spaces.publish_files("hybrid-bonding", [("index.md", b"# i\n"), ("../evil.md", b"# e\n")])
    assert storage.writes == []


async def test_publish_creates_the_space_prefix_and_lists_it(storage):
    """首次发布不需要预先建桶或目录，列表按前缀聚合。"""
    result = await spaces.publish_files("hybrid-bonding", [("sub/note.md", b"# n\n")])
    assert result["saved"] == ["sub/note.md"]
    assert storage.objects["hybrid-bonding/sub/note.md"] == b"# n\n"
    assert await spaces.list_space_files("hybrid-bonding") == ["index.md", "sub/note.md"]
    assert {item["slug"]: item["files"] for item in await spaces.list_spaces()} == {
        "hybrid-bonding": 2,
        "old-space": 1,
    }


async def test_delete_space_only_touches_its_own_prefix(storage):
    """删除空间不能波及别的空间。"""
    assert await spaces.delete_space("old-space") == 1
    assert storage.objects == {"hybrid-bonding/index.md": b"# index\n"}
