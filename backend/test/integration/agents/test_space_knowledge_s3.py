"""真实对象存储上的空间只读视图：发布、列举、读取、检索与挂载点回映射。

这些用例用一个随机前缀模拟开发者发布空间，退出时删掉该前缀，不依赖知识库或 LLM。
"""

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from deepagents.backends.protocol import BackendProtocol, LsResult

from yuxi.infrastructure.minio import aupload_file_to_minio, get_minio_client
from yuxi.modules.agents.runtime.sandbox import backend as backend_module
from yuxi.modules.agents.runtime.sandbox.backend import create_agent_composite_backend
from yuxi.modules.agents.runtime.sandbox.space_knowledge import SPACES_BUCKET, SpaceKnowledgeBackend

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.self_managed_resources]

FIXTURE_SPACE = "hybrid-bonding"
SOURCE = Path(__file__).resolve().parents[2] / "data" / "spaces" / FIXTURE_SPACE


class StubDefault(BackendProtocol):
    """默认后端替身：只证明根级列举不受空间路由影响。"""

    def ls(self, path: str) -> LsResult:
        return LsResult(entries=[])

    async def als(self, path: str) -> LsResult:
        return self.ls(path)


@pytest.fixture
async def published():
    """把夹具空间按发布布局写进真实桶，测试结束只删自己写的前缀。"""
    slug = f"pytest-space-{uuid.uuid4().hex[:8]}"
    client = get_minio_client()
    assert client.ensure_bucket_exists(SPACES_BUCKET)
    files = {path.relative_to(SOURCE).as_posix(): path.read_bytes() for path in sorted(SOURCE.rglob("*.md"))}
    for relative, data in files.items():
        await aupload_file_to_minio(SPACES_BUCKET, f"{slug}/{relative}", data)
    yield slug, files
    await client.adelete_objects_by_prefix(SPACES_BUCKET, f"{slug}/")


async def test_lists_and_reads_the_published_space(published):
    """列举出的每个路径都能读回原文件内容；行号由中间件加，这里保持原文。"""
    slug, files = published
    backend = SpaceKnowledgeBackend(space_slug=slug)
    listing = await backend.als("/")
    assert {entry["path"] for entry in listing.entries} == {f"/{relative}" for relative in files}
    page = await backend.aread("/index.md")
    assert page.error is None and page.file_data["content"] == files["index.md"].decode("utf-8")


async def test_searches_real_object_content(published):
    """内容检索命中真实对象正文并给出行号，且不跨前缀。"""
    slug, files = published
    backend = SpaceKnowledgeBackend(space_slug=slug)
    matches = (await backend.agrep("空洞")).matches
    assert matches, "夹具里必然出现「空洞」"
    expected = next(path for path, data in files.items() if "空洞" in data.decode("utf-8"))
    assert matches[0]["path"] == f"/{expected}"
    assert (await backend.agrep("CBAM 申报")).matches == []


async def test_composite_exposes_space_under_its_mount_point(published, monkeypatch):
    """按真实装配方式挂载后，模型看到的是 /spaces/ 前缀而不是裸空间路径。

    默认后端需要可用的沙盒 VM，它的列举不在本用例范围；路由与回映射用真实存储内容验证。
    """
    slug, _ = published
    monkeypatch.setattr(backend_module, "UserSandboxBackend", lambda **_kwargs: StubDefault())
    context = SimpleNamespace(space_slug=slug, uid=f"pytest-{uuid.uuid4().hex[:8]}", workdir_relative_path="pytest-space")
    composite = create_agent_composite_backend(context)
    assert "/spaces/" in {entry["path"] for entry in (await composite.als("/")).entries}
    assert "/spaces/index.md" in {entry["path"] for entry in (await composite.als("/spaces")).entries}
    assert (await composite.aread("/spaces/index.md")).error is None
    assert (await composite.agrep("空洞", path="/spaces")).matches
