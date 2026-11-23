"""Skill 专有连接器的标记与释放规则。"""

from types import SimpleNamespace

import pytest

from yuxi.modules.extensions.mcp import service as mcp_service


class FakeResult:
    """模拟 SQLAlchemy 结果的最小接口。"""

    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items


class FakeSession:
    """只记录提交次数，避免依赖真实数据库。"""

    def __init__(self, servers):
        self._servers = servers
        self.commits = 0

    async def execute(self, _statement):
        return FakeResult(self._servers)

    async def commit(self):
        self.commits += 1


def _server(slug, *, created_by="admin", origin_skill=None, enabled=0):
    return SimpleNamespace(
        slug=slug,
        created_by=created_by,
        origin_skill=origin_skill,
        enabled=enabled,
        updated_by=None,
    )


@pytest.fixture
def saved(monkeypatch):
    store = {}
    saved_slugs = []

    async def fake_get_mcp_server(_db, slug, *, lock=False):
        return store.get(slug)

    async def fake_save_mcp_server(_db, server, *, new=False):
        saved_slugs.append(server.slug)

    monkeypatch.setattr(mcp_service, "get_mcp_server", fake_get_mcp_server)
    monkeypatch.setattr(mcp_service, "save_mcp_server", fake_save_mcp_server)
    return store


async def test_mark_skips_connectors_created_by_others(saved):
    """别人建的连接器即使列在依赖里也不算本 Skill 专有。"""
    saved["alpha"] = _server("alpha", created_by="someone-else")

    await mcp_service.mark_skill_owned_mcp_servers(
        FakeSession([]),
        slugs=["alpha"],
        claimed_slugs={"alpha"},
        skill_slug="travel",
        operator=SimpleNamespace(username="admin"),
    )

    assert saved["alpha"].origin_skill is None


async def test_mark_enables_own_connector_and_reuses_existing_origin(saved):
    """自己的连接器被标记并启用，重装同 slug 时恢复启用（不在本次新建列表也生效）。"""
    saved["alpha"] = _server("alpha", origin_skill="travel")

    await mcp_service.mark_skill_owned_mcp_servers(
        FakeSession([]),
        slugs=["alpha"],
        claimed_slugs=set(),
        skill_slug="travel",
        operator=SimpleNamespace(username="admin"),
    )

    assert saved["alpha"].origin_skill == "travel"
    assert saved["alpha"].enabled == 1


async def test_release_keeps_connector_referenced_by_other_skill(monkeypatch, saved):
    """删除 Skill 后仍有别的 Skill 引用，保留启用且不改归属。"""
    server = _server("alpha", origin_skill="travel", enabled=1)
    saved["alpha"] = server
    remaining = SimpleNamespace(id=7, slug="travel-v2")

    class FakeSkillRepo:
        def __init__(self, _db):
            pass

        async def list_referencing_mcp(self, slug, *, exclude_id=None):
            return [remaining]

    class FakeAgentRepo:
        def __init__(self, _db):
            pass

        async def list_referencing_mcp(self, slug):
            return []

    monkeypatch.setattr("yuxi.modules.extensions.skills.repository.SkillRepository", FakeSkillRepo)
    monkeypatch.setattr("yuxi.modules.agents.repositories.definitions.AgentRepository", FakeAgentRepo)
    monkeypatch.setattr(mcp_service, "save_mcp_server", _noop_save)

    await mcp_service.release_skill_owned_mcp_servers(FakeSession([server]), mcp_slugs=["alpha"], skill_id=1)

    assert server.origin_skill == "travel"
    assert server.enabled == 1


async def test_release_keeps_connector_used_by_agent(monkeypatch, saved):
    """被智能体显式引用时不静默停用。"""
    server = _server("alpha", origin_skill="travel", enabled=1)
    saved["alpha"] = server

    class FakeSkillRepo:
        def __init__(self, _db):
            pass

        async def list_referencing_mcp(self, slug, *, exclude_id=None):
            return []

    class FakeAgentRepo:
        def __init__(self, _db):
            pass

        async def list_referencing_mcp(self, slug):
            return ["agent-a"]

    monkeypatch.setattr("yuxi.modules.extensions.skills.repository.SkillRepository", FakeSkillRepo)
    monkeypatch.setattr("yuxi.modules.agents.repositories.definitions.AgentRepository", FakeAgentRepo)
    monkeypatch.setattr(mcp_service, "save_mcp_server", _noop_save)

    await mcp_service.release_skill_owned_mcp_servers(FakeSession([server]), mcp_slugs=["alpha"], skill_id=1)

    assert server.enabled == 1


async def test_release_disables_unreferenced_connector(monkeypatch, saved):
    """无人引用的专有连接器在删除 Skill 后停用。"""
    server = _server("alpha", origin_skill="travel", enabled=1)
    saved["alpha"] = server

    class EmptyRepo:
        def __init__(self, _db):
            pass

        async def list_referencing_mcp(self, slug, *, exclude_id=None):
            return []

    monkeypatch.setattr("yuxi.modules.extensions.skills.repository.SkillRepository", EmptyRepo)
    monkeypatch.setattr("yuxi.modules.agents.repositories.definitions.AgentRepository", EmptyRepo)
    monkeypatch.setattr(mcp_service, "save_mcp_server", _noop_save)

    await mcp_service.release_skill_owned_mcp_servers(FakeSession([server]), mcp_slugs=["alpha"], skill_id=1)

    assert server.enabled == 0


async def _noop_save(_db, _server, *, new=False):
    return None
