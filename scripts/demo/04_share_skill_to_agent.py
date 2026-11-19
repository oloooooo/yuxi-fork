"""步骤 4：上传 skill 并以 global 共享，再把该 skill 分配给指定 agent。

流程：POST /api/skills/import/prepare（草稿）
  → POST /api/skills/install-drafts/{draft_id}/confirm（read_scope=global，需管理员）
  → PUT /api/agent/{slug}（config_json.context.skills 加入该 skill）

用法：
  python3 scripts/demo/04_share_skill_to_agent.py --skill my-skill.zip --agent default-chatbot
"""

import argparse
import json

import yuxi_api

GLOBAL_SHARE = {
    "version": 2,
    "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
    "manage_scope": None,
}


def main():
    parser = argparse.ArgumentParser(description="建立共享 skill 并分配给 agent")
    parser.add_argument("--skill", required=True, help="skill 的 .zip（内含唯一 SKILL.md）或 SKILL.md")
    parser.add_argument("--agent", required=True, help="接收该 skill 的 agent slug")
    parser.add_argument("--base-url", help="API 地址，默认取 YUXI_BASE_URL")
    parser.add_argument("--token", help="access token，默认取 YUXI_ACCESS_TOKEN")
    args = parser.parse_args()

    client, base, token = yuxi_api.session(args)

    prepared = yuxi_api.post_multipart(base, token, "/skills/import/prepare", {}, [("file", args.skill)])
    draft = prepared["data"]
    if not draft["items"]:
        raise SystemExit("skill 解析失败：%s" % json.dumps(draft.get("failures"), ensure_ascii=False))
    slug = draft["items"][0]["slug"]
    print("1/3 已上传草稿 %s（skill slug=%s）" % (draft["draft_id"], slug))

    confirmed = client.call(
        "POST",
        "/skills/install-drafts/%s/confirm" % draft["draft_id"],
        {"slugs": [slug], "share_config": GLOBAL_SHARE},
    )
    if not confirmed.get("success"):
        raise SystemExit("确认安装失败：%s" % json.dumps(confirmed, ensure_ascii=False))
    print("2/3 已安装并共享（read_scope=global）")

    agent = client.call("GET", "/agent/%s" % args.agent)["agent"]
    skills = (agent.get("config_json") or {}).get("context", {}).get("skills")
    if isinstance(skills, list) and slug in skills:
        print("3/3 agent %s 已包含该 skill，未修改" % args.agent)
    else:
        updated = [slug] if not isinstance(skills, list) else [*skills, slug]
        client.call("PUT", "/agent/%s" % args.agent, {"config_json": {"context": {"skills": updated}}})
        if skills == "all":
            print("3/3 已把 agent %s 的 skills 由 all 收窄为 %s" % (args.agent, updated))
        else:
            print("3/3 已把 %s 分配给 agent %s（skills=%s）" % (slug, args.agent, updated))
    return 0


if __name__ == "__main__":
    raise SystemExit(yuxi_api.run(main))
