"""部门空间以共享 Skill 挂载到子 Agent 的真实 HTTP 与运行时证据。"""

from __future__ import annotations

import os
import uuid
from io import BytesIO
from zipfile import ZipFile

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.modules.agents.runtime.agent_backends.chatbot.context import ChatBotContext
from yuxi.modules.extensions.skills.projection import get_user_skills_root_dir, refresh_user_skill_projection_async
from yuxi.modules.extensions.skills.runtime import resolve_runtime_skills_for_context
from yuxi.modules.identity.models import User

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture(autouse=True)
async def close_projection_pool():
    """投影用例的连接池在所属测试事件循环退出前关闭。"""
    yield
    from yuxi.infrastructure.postgres.manager import pg_manager

    await pg_manager.close()


def _space_skill_zip(slug: str) -> bytes:
    """构造带参考文件的最小业务空间 Skill 包。"""
    result = BytesIO()
    with ZipFile(result, "w") as archive:
        archive.writestr(
            f"{slug}/SKILL.md",
            f"---\nslug: {slug}\nname: {slug}\ndescription: {slug} 空间知识\n---\n"
            f"# {slug}\n\n先读 `references/index.md`，再读 `references/rules.md`。\n",
        )
        archive.writestr(f"{slug}/references/index.md", "# 空间索引\n")
        archive.writestr(f"{slug}/references/rules.md", "空间规则正文\n")
    return result.getvalue()


async def test_department_space_skill_mounts_on_sub_agent(actors):
    """部门用户同时可见空间 Skill 与子 Agent；运行时预加载并投影。"""
    client, root = actors["client"], actors["root"]
    admin = actors["identities"]["admin0"]["headers"]
    scope = uuid.uuid4().hex[:8]
    space_slug = f"pytest-space-{scope}"
    department_id = actors["identities"]["user0"]["department_id"]
    share_config = {
        "version": 2,
        "read_scope": {"access_level": "department", "department_ids": [department_id], "user_uids": []},
        "manage_scope": None,
    }
    draft_id = None
    agent_created = False
    try:
        prepared = await client.post(
            "/api/skills/import/prepare",
            headers=admin,
            files={"file": ("space.zip", _space_skill_zip(space_slug), "application/zip")},
        )
        assert prepared.status_code == 200, prepared.text
        draft_id = prepared.json()["data"]["draft_id"]
        installed = await client.post(
            f"/api/skills/install-drafts/{draft_id}/confirm",
            headers=admin,
            json={"slugs": [space_slug], "share_config": share_config},
        )
        assert installed.status_code == 200, installed.text
        assert installed.json()["data"][0]["success"] is True, installed.text

        created = await client.post(
            "/api/agent",
            headers=admin,
            json={
                "slug": space_slug,
                "name": space_slug,
                "visibility": "shared",
                "share_config": share_config,
                "config_json": {
                    "context": {
                        "system_prompt": "你是业务空间子 Agent。",
                        "skills": [space_slug],
                        "preload_skills": [space_slug],
                    }
                },
            },
        )
        assert created.status_code == 200, created.text
        agent_created = True
        actors["agents"].append(space_slug)

        user0, user1 = actors["identities"]["user0"], actors["identities"]["user1"]
        visible = await client.get(f"/api/agent/{space_slug}", headers=user0["headers"])
        assert visible.status_code == 200, visible.text
        context_config = visible.json()["agent"]["config_json"]["context"]
        assert context_config["skills"] == [space_slug]
        assert context_config["preload_skills"] == [space_slug]
        assert (await client.get(f"/api/agent/{space_slug}", headers=user1["headers"])).status_code == 404

        accessible = await client.get("/api/skills/accessible", headers=user0["headers"])
        assert accessible.status_code == 200, accessible.text
        assert space_slug in {item["slug"] for item in accessible.json()["data"]}
        hidden = await client.get("/api/skills/accessible", headers=user1["headers"])
        assert hidden.status_code == 200, hidden.text
        assert space_slug not in {item["slug"] for item in hidden.json()["data"]}
        assert (await client.get("/api/spaces", headers=root)).status_code == 404

        engine = create_async_engine(os.environ["POSTGRES_URL"])
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as db:
                user = await db.scalar(select(User).where(User.uid == user0["uid"]))
                await refresh_user_skill_projection_async(user.uid)
                context = ChatBotContext(uid=user.uid, agent_slug=space_slug)
                context.update_config(context_config)
                snapshot = await resolve_runtime_skills_for_context(context, db=db, user=user)
            assert snapshot["effective_skills"] == [space_slug]
            assert snapshot["preloaded_skills"] == [space_slug]
            preloaded = snapshot["preloaded_skill_contents"][space_slug]
            assert f"# {space_slug}" in preloaded
            assert "空间规则正文" not in preloaded
            projection = get_user_skills_root_dir(user0["uid"]) / space_slug
            assert (projection / "SKILL.md").is_file()
            assert (projection / "references/rules.md").read_text(encoding="utf-8") == "空间规则正文\n"

            async with factory() as db:
                other = await db.scalar(select(User).where(User.uid == user1["uid"]))
                await refresh_user_skill_projection_async(other.uid)
                hidden_context = ChatBotContext(uid=other.uid, agent_slug=space_slug)
                hidden_context.update_config(context_config)
                with pytest.raises(PermissionError, match="智能体不存在或无权限访问"):
                    await resolve_runtime_skills_for_context(hidden_context, db=db, user=other)
            assert not (get_user_skills_root_dir(user1["uid"]) / space_slug).exists()

            revoked = await client.put(
                f"/api/system/skills/{space_slug}/share-config",
                headers=root,
                json={
                    "share_config": {
                        "version": 2,
                        "read_scope": {
                            "access_level": "department",
                            "department_ids": [user1["department_id"]],
                            "user_uids": [],
                        },
                        "manage_scope": None,
                    }
                },
            )
            assert revoked.status_code == 200, revoked.text
            assert (await client.get(f"/api/agent/{space_slug}", headers=user0["headers"])).status_code == 200
            async with factory() as db:
                user = await db.scalar(select(User).where(User.uid == user0["uid"]))
                await refresh_user_skill_projection_async(user.uid)
                context = ChatBotContext(uid=user.uid, agent_slug=space_slug)
                context.update_config(context_config)
                snapshot = await resolve_runtime_skills_for_context(context, db=db, user=user)
            assert snapshot["effective_skills"] == []
            assert snapshot["preloaded_skills"] == []
            assert not (get_user_skills_root_dir(user0["uid"]) / space_slug).exists()
        finally:
            await engine.dispose()
    finally:
        if agent_created:
            await client.delete(f"/api/agent/{space_slug}", headers=root)
        if draft_id is not None:
            await client.delete(f"/api/skills/install-drafts/{draft_id}", headers=root)
        await client.delete(f"/api/system/skills/{space_slug}", headers=root)
