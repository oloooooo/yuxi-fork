"""scripts/demo 共用的小工具：读取本地凭据、装配 yuxi_client、发送 multipart 请求。"""

import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from yuxi_client import Client, YuxiError

ENV_FILE = Path(__file__).with_name(".env.local")
DEFAULT_BASE = "http://127.0.0.1:5050"
TOKEN_HINT = (
    "缺少 access token：先运行 "
    '`eval "$(python3 scripts/demo/01_get_token.py --uid <用户ID> --password <密码>)"`'
)

# Windows 控制台默认 cp936，直接打印中文会乱码，统一改成 UTF-8 输出。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and (_stream.encoding or "").lower() not in ("utf-8", "utf8"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def load_env_file():
    """把 .env.local 的 KEY=VALUE 载入环境变量，已存在的变量优先。"""
    if not ENV_FILE.is_file():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def base_url(cli_value=None):
    """命令行 > 环境变量 > 本地默认地址。"""
    return (cli_value or os.environ.get("YUXI_BASE_URL") or DEFAULT_BASE).rstrip("/")


def session(args):
    """返回 (client, base_url, token)；缺 token 时打印获取方式并退出。"""
    load_env_file()
    token = getattr(args, "token", None) or os.environ.get("YUXI_ACCESS_TOKEN") or os.environ.get("TOKEN")
    if not token:
        sys.exit(TOKEN_HINT)
    base = base_url(getattr(args, "base_url", None))
    return Client(base, token), base, token


def run(main):
    """执行脚本主函数：把后端错误压成一行提示并返回非零码，不抛出 traceback。"""
    try:
        return main()
    except YuxiError as exc:
        print("失败：%s" % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def post_multipart(base, token, path, fields, files, timeout=600):
    """POST multipart/form-data 到 /api+path；files 为 [(字段名, 本地路径)]。"""
    boundary = "----yuxi-demo-%s" % uuid.uuid4().hex
    body = bytearray()
    for name, value in fields.items():
        body += (
            '--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, name, value)
        ).encode("utf-8")
    for name, file_path in files:
        source = Path(file_path)
        if not source.is_file():
            sys.exit("文件不存在：%s" % file_path)
        body += (
            '--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n" % (boundary, name, source.name)
        ).encode("utf-8")
        body += source.read_bytes() + b"\r\n"
    body += ("--%s--\r\n" % boundary).encode("utf-8")

    request = urllib.request.Request(base + "/api" + path, data=bytes(body), method="POST")
    request.add_header("Authorization", "Bearer " + token)
    request.add_header("Content-Type", "multipart/form-data; boundary=" + boundary)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace").strip()[:500]
        raise YuxiError("HTTP %d %s：%s" % (exc.code, path, detail)) from exc
    except urllib.error.URLError as exc:
        raise YuxiError("无法连接 %s：%s" % (base, exc.reason)) from exc
