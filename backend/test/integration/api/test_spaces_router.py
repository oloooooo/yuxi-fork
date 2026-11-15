"""业务空间发布接口的管理员边界：真实 HTTP 下管理员可发布，普通用户被拒。"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture
def space_slug():
    return f"pytest-space-{uuid.uuid4().hex[:8]}"


async def test_admin_publishes_lists_and_deletes_a_space(test_client, admin_headers, space_slug):
    """发布、列举、删除都打在同一个前缀上，且不污染其它空间。"""
    publish = await test_client.post(
        f"/api/spaces/{space_slug}/files",
        headers=admin_headers,
        data={"paths": ["index.md", "sub/note.md"]},
        files=[
            ("files", ("index.md", b"# index\n", "text/markdown")),
            ("files", ("note.md", b"# note\n", "text/markdown")),
        ],
    )
    assert publish.status_code == 200, publish.text
    assert publish.json()["saved"] == ["index.md", "sub/note.md"]

    try:
        listing = await test_client.get(f"/api/spaces/{space_slug}", headers=admin_headers)
        assert listing.status_code == 200, listing.text
        assert listing.json()["files"] == ["index.md", "sub/note.md"]

        spaces = await test_client.get("/api/spaces", headers=admin_headers)
        assert spaces.status_code == 200, spaces.text
        assert space_slug in {item["slug"] for item in spaces.json()["spaces"]}

        removed = await test_client.delete(f"/api/spaces/{space_slug}", headers=admin_headers)
        assert removed.status_code == 200, removed.text
        assert removed.json()["deleted"] == 2
    finally:
        await test_client.delete(f"/api/spaces/{space_slug}", headers=admin_headers)


async def test_non_markdown_and_traversal_are_refused_over_http(test_client, admin_headers, space_slug):
    """非法路径必须由接口拒绝，而不是写进前缀。"""
    for path in ["../evil.md", "notes.txt"]:
        response = await test_client.post(
            f"/api/spaces/{space_slug}/files",
            headers=admin_headers,
            data={"paths": [path]},
            files=[("files", ("x.md", b"# x\n", "text/markdown"))],
        )
        assert response.status_code == 400, response.text


async def test_standard_user_is_refused(test_client, standard_user, space_slug):
    """普通用户没有发布入口，读取与删除同样被拒。"""
    response = await test_client.post(
        f"/api/spaces/{space_slug}/files",
        headers=standard_user["headers"],
        data={"paths": ["index.md"]},
        files=[("files", ("index.md", b"# index\n", "text/markdown"))],
    )
    assert response.status_code == 403, response.text
    assert (await test_client.get("/api/spaces", headers=standard_user["headers"])).status_code == 403
    assert (await test_client.delete(f"/api/spaces/{space_slug}", headers=standard_user["headers"])).status_code == 403
