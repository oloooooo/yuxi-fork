"""步骤 3：列出当前用户可见的所有 agent 及其 slug。

用法：
  python3 scripts/demo/03_list_agents.py           # 表格
  python3 scripts/demo/03_list_agents.py --slug-only
"""

import argparse
import sys

import yuxi_api


def main():
    parser = argparse.ArgumentParser(description="列出当前用户可用的 agent")
    parser.add_argument("--slug-only", action="store_true", help="只输出 slug，一行一个，便于管道使用")
    parser.add_argument("--base-url", help="API 地址，默认取 YUXI_BASE_URL")
    parser.add_argument("--token", help="access token，默认取 YUXI_ACCESS_TOKEN")
    args = parser.parse_args()

    client, _base, _token = yuxi_api.session(args)
    agents = (client.call("GET", "/agent") or {}).get("agents") or []
    if not agents:
        print("当前没有可用 agent", file=sys.stderr)
        return 0

    if args.slug_only:
        for agent in agents:
            print(agent.get("slug"))
        return 0

    print("slug\tname\tvisibility")
    for agent in agents:
        print("%s\t%s\t%s" % (agent.get("slug"), agent.get("name"), agent.get("visibility")))
    return 0


if __name__ == "__main__":
    raise SystemExit(yuxi_api.run(main))
