"""步骤 1：密码登录换取 access token，输出 YUXI_BASE_URL / YUXI_ACCESS_TOKEN。

用法：
  eval "$(python3 scripts/demo/01_get_token.py --uid admin --password '***')"
凭据同时写入 scripts/demo/.env.local（已被 .gitignore 忽略），供后续脚本自动读取。
"""

import argparse
import getpass
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yuxi_api


def main():
    parser = argparse.ArgumentParser(description="登录 Yuxi 并输出 access token 环境变量")
    parser.add_argument("--base-url", help="API 地址，默认取 YUXI_BASE_URL 或 http://127.0.0.1:5050")
    parser.add_argument("--uid", default=os.environ.get("YUXI_UID", ""), help="登录标识：用户 ID 或手机号")
    parser.add_argument("--password", default=os.environ.get("YUXI_PASSWORD", ""), help="登录密码，缺省时交互输入")
    parser.add_argument("--save", default=str(yuxi_api.ENV_FILE), help="写出环境变量的文件")
    args = parser.parse_args()

    interactive = sys.stdin.isatty()
    # 终端存在但读不到输入（例如被管道/重定向）时按缺省处理，不抛 traceback。
    try:
        uid = args.uid or (input("用户ID: ").strip() if interactive else "")
        password = args.password or (getpass.getpass("密码: ") if interactive else "")
    except EOFError:
        uid, password = args.uid, args.password
    if not uid or not password:
        print("缺少 --uid / --password（非交互环境无法提示输入）", file=sys.stderr)
        return 2

    base = yuxi_api.base_url(args.base_url)
    data = urllib.parse.urlencode({"username": uid, "password": password}).encode("utf-8")
    request = urllib.request.Request(base + "/api/auth/token", data=data, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            token = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print("登录失败 HTTP %d：%s" % (exc.code, exc.read().decode("utf-8", "replace")[:300]), file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print("无法连接 %s：%s" % (base, exc.reason), file=sys.stderr)
        return 1

    value = token["access_token"]
    saved = Path(args.save)
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text("YUXI_BASE_URL=%s\nYUXI_ACCESS_TOKEN=%s\n" % (base, value), encoding="utf-8")
    try:
        saved.chmod(0o600)
    except OSError:
        pass

    print("export YUXI_BASE_URL=%s" % base, flush=True)
    print("export YUXI_ACCESS_TOKEN=%s" % value, flush=True)
    print("已登录 %s（角色 %s），凭据写入 %s" % (token.get("uid") or uid, token.get("role"), saved), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(yuxi_api.run(main))
