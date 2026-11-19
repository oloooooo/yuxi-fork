"""步骤 2：创建私有 agent，并把 zip 或 SKILL.md 直接绑定为它的专属 skill。

用法：
  python3 scripts/demo/02_agent_with_skill.py --skill my-skill.zip --name "我的私有技能助手"
"""

import argparse
import json
from pathlib import Path

import yuxi_api


def main():
    parser = argparse.ArgumentParser(description="创建私有 agent 并绑定专属 skill")
    parser.add_argument("--skill", required=True, help="skill 的 .zip（内含唯一 SKILL.md）或 SKILL.md")
    parser.add_argument("--name", required=True, help="agent 显示名")
    parser.add_argument("--slug", help="自定义 slug，默认由后端按名称生成")
    parser.add_argument("--backend", default="ChatbotAgent", help="AgentBackend ID，默认 ChatbotAgent")
    parser.add_argument("--description", default="", help="agent 描述")
    parser.add_argument("--base-url", help="API 地址，默认取 YUXI_BASE_URL")
    parser.add_argument("--token", help="access token，默认取 YUXI_ACCESS_TOKEN")
    args = parser.parse_args()

    _client, base, token = yuxi_api.session(args)
    payload = {
        "name": args.name,
        "description": args.description,
        "backend_id": args.backend,
        "visibility": "private",
    }
    if args.slug:
        payload["slug"] = args.slug

    data = yuxi_api.post_multipart(
        base,
        token,
        "/agent/with-skill",
        {"agent": json.dumps(payload, ensure_ascii=False)},
        [("file", args.skill)],
    )
    agent = data["agent"]
    print("已创建 agent：%s（slug=%s，visibility=%s）" % (agent["name"], agent["slug"], agent.get("visibility")))
    print("已绑定专属 skill 文件：%s" % Path(args.skill).name)
    print("下一步可用 03_list_agents.py 看到它，或用 scripts/demo/yuxi_client.py 以该 slug 发起会话。")
    return 0


if __name__ == "__main__":
    raise SystemExit(yuxi_api.run(main))
