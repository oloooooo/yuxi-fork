#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Yuxi v0.8.0 Public v1 API 本地客户端（仅标准库，Python 3.8+）。

环境变量：
    SPEC_MODEL   模型 spec，形如 provider_id:model_id（建会话时生效）
    TOKEN        后端 JWT
    BASE         后端地址，默认 http://127.0.0.1:5050

用法：
    export BASE=http://127.0.0.1:5050
    export SPEC_MODEL=my-gw:gpt-4o-mini
    export TOKEN=<curl 登录拿到的 access_token>
    python3 scripts/demo/yuxi_client.py --agent default-chatbot --file ./data.xlsx

进入交互：
    分析这张表            普通文字 = 发消息（带上待发文件）
    /file ./b.xlsx        上传草稿，不发送；下一条消息自动带上
    /file ./c.xlsx 再看   上传并立即发送
    /agents              列出可指定的 Agent
    /use <slug>          切换 Agent（下一条消息开新会话）
    /new                  开新会话
    /turns                查看本会话的轮次
    /exit                 退出
"""

import argparse
import json
import mimetypes
import os
import sys
import time
import uuid
from urllib import error, request

MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
SETTLED = ("completed", "failed", "cancelled", "requires_action")
TERMINAL_EVENTS = (
    "agent.session.turn.completed",
    "agent.session.turn.failed",
    "agent.session.turn.cancelled",
    "yuxi.session.turn.waiting",
)


class YuxiError(Exception):
    """后端返回的业务错误。"""


def _detail(raw: bytes, status: int) -> str:
    text = raw.decode("utf-8", "replace")
    try:
        data = json.loads(text)
    except ValueError:
        return text[:400] or "HTTP %d" % status
    if isinstance(data, dict):
        for key in ("detail", "message", "error"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, list) and value:
                return "; ".join(
                    str(v.get("msg", v)) if isinstance(v, dict) else str(v) for v in value
                )
    return json.dumps(data, ensure_ascii=False)[:400]


def _http(method, url, headers=None, body=None, timeout=180):
    req = request.Request(url, data=body, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except error.HTTPError as exc:
        raise YuxiError("HTTP %d: %s" % (exc.code, _detail(exc.read(), exc.code))) from exc
    except error.URLError as exc:
        raise YuxiError("请求失败 %s：%s" % (url, exc.reason)) from exc


def _multipart(field, filename, content, content_type):
    boundary = "----yuxi" + uuid.uuid4().hex
    head = (
        "--%s\r\n" % boundary
        + 'Content-Disposition: form-data; name="%s"; filename="%s"\r\n' % (field, filename)
        + "Content-Type: %s\r\n\r\n" % content_type
    ).encode("utf-8")
    tail = ("\r\n--%s--\r\n" % boundary).encode("utf-8")
    return head + content + tail, "multipart/form-data; boundary=%s" % boundary


class Client:
    """Public v1 Agents API 的最小客户端。"""

    def __init__(self, base, token):
        self.api = base.rstrip("/") + "/api"
        self.token = token

    def _headers(self, extra=None):
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        headers.update(extra or {})
        return headers

    def call(self, method, path, payload=None, headers=None, timeout=180):
        body = None
        head = self._headers(headers)
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            head["Content-Type"] = "application/json"
        raw = _http(method, self.api + path, headers=head, body=body, timeout=timeout)
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            raise YuxiError("响应不是 JSON：%r" % raw[:200])

    # --- 资源 ---
    def health(self):
        raw = _http(
            "GET",
            self.api + "/system/health",
            headers={"Accept": "application/json"},
            timeout=15,
        )
        return json.loads(raw.decode("utf-8")) if raw else {}

    def upload_file(self, path):
        if not os.path.isfile(path):
            raise YuxiError("文件不存在：%s" % path)
        with open(path, "rb") as handle:
            content = handle.read()
        if len(content) > MAX_ATTACHMENT_BYTES:
            raise YuxiError(
                "%s 超过 5 MiB 附件上限（%d 字节）。大文件请改走工作区上传："
                "POST /api/workspace/upload，再在提问里给绝对路径。" % (path, len(content))
            )
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        body, content_type = _multipart("file", os.path.basename(path), content, ctype)
        raw = _http(
            "POST",
            self.api + "/v1/agents/files",
            headers=self._headers({"Content-Type": content_type}),
            body=body,
        )
        data = json.loads(raw.decode("utf-8"))
        if not data.get("id"):
            raise YuxiError("上传未返回文件 id：%r" % data)
        return data["id"]

    def create_session(self, agent_id, model):
        """建空会话；模型覆盖在创建时固定，后续每轮沿用。"""
        payload = {"agent_id": agent_id}
        if model:
            payload["agent"] = {"model": model}
        key = str(uuid.uuid4())
        data = self.call(
            "POST", "/v1/agents/sessions", payload, {"Idempotency-Key": key}
        )
        return data, key

    def send_event(self, session_id, text, file_ids, key):
        yuxi = {"mode": "follow_up"}
        if file_ids:
            yuxi["attachment_file_ids"] = list(file_ids)
        payload = {
            "events": [
                {
                    "type": "agent.session.input.message",
                    "input": [
                        {"role": "user", "content": [{"type": "input_text", "text": text}]}
                    ],
                    "yuxi": yuxi,
                }
            ]
        }
        return self.call(
            "POST",
            "/v1/agents/sessions/%s/events" % session_id,
            payload,
            {"Idempotency-Key": key},
        )

    def get_input(self, session_id, input_id):
        return self.call("GET", "/v1/agents/sessions/%s/inputs/%s" % (session_id, input_id))

    def get_turn(self, session_id, turn_id):
        return self.call("GET", "/v1/agents/sessions/%s/turns/%s" % (session_id, turn_id))

    def list_turns(self, session_id):
        return self.call("GET", "/v1/agents/sessions/%s/turns" % session_id)

    def list_agents(self):
        return self.call("GET", "/v1/agents/")

    def receipt(self, session_id, key):
        return self.call(
            "GET", "/v1/agents/sessions/%s/receipt?idempotency_key=%s" % (session_id, key)
        )

    def sse(self, session_id, cursor=None, timeout=300):
        """订阅会话事件流，产出 {"id":..,"event":..,"data":{..}}。"""
        headers = {"Accept": "text/event-stream"}
        if cursor:
            headers["Last-Event-ID"] = cursor
        req = request.Request(
            "%s/v1/agents/sessions/%s/events" % (self.api, session_id), method="GET"
        )
        for key, value in self._headers(headers).items():
            req.add_header(key, value)
        try:
            resp = request.urlopen(req, timeout=timeout)
        except error.HTTPError as exc:
            raise YuxiError("HTTP %d: %s" % (exc.code, _detail(exc.read(), exc.code))) from exc
        except error.URLError as exc:
            raise YuxiError("事件流连接失败：%s" % exc.reason) from exc

        with resp:
            frame = {}
            data_lines = []
            for raw_line in resp:
                line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
                if line == "":
                    if data_lines:
                        frame["data"] = _parse_json("\n".join(data_lines))
                        yield frame
                    frame = {}
                    data_lines = []
                    continue
                if line.startswith(":"):
                    continue
                field, _, value = line.partition(":")
                if value.startswith(" "):
                    value = value[1:]
                if field == "data":
                    data_lines.append(value)
                elif field in ("event", "id"):
                    frame[field] = value
            if data_lines:
                frame["data"] = _parse_json("\n".join(data_lines))
                yield frame


def _parse_json(text):
    try:
        return json.loads(text or "{}")
    except ValueError:
        return {}


def _is_settled(snapshot):
    return str(snapshot.get("status")) in SETTLED


def _pad(text, width):
    """按显示宽度补空格：中日韩字符占两列。"""
    size = sum(2 if ord(char) > 0x2E80 else 1 for char in text)
    return text + " " * max(0, width - size)


def _turn_text(snapshot):
    items = (snapshot.get("yuxi") or {}).get("output") or []
    parts = []
    for item in items:
        if item.get("type") != "message" or item.get("role") != "assistant":
            continue
        for part in item.get("content") or []:
            if part.get("type") == "output_text":
                parts.append(str(part.get("text") or ""))
    return "".join(parts)


class ChatSession:
    """一轮对话：提交 → 等 turn → 读结果。"""

    def __init__(self, client, session_id):
        self.client = client
        self.session_id = session_id
        self.cursor = None

    def send(self, text, file_ids, out=sys.stdout):
        key = str(uuid.uuid4())
        try:
            accepted = self.client.send_event(self.session_id, text, file_ids, key)
        except YuxiError as exc:
            raise YuxiError("%s\n（幂等键 %s，重试请复用它）" % (exc, key)) from exc
        input_id = accepted.get("input_id")
        if not input_id:
            raise YuxiError("回执缺少 input_id：%r" % accepted)

        turn_id = ""
        deadline = time.time() + 120
        while not turn_id:
            info = self.client.get_input(self.session_id, input_id)
            if str(info.get("status")) == "cancelled":
                raise YuxiError("输入已取消")
            turn_id = str(info.get("turn_id") or "")
            if not turn_id:
                if time.time() > deadline:
                    raise YuxiError("等待 turn 超时（input %s）" % input_id)
                time.sleep(1)

        snapshot = self._await_turn(turn_id, out)
        status = str(snapshot.get("status"))
        if status == "requires_action":
            out.write(
                "\n[本轮需要你在 Yuxi 网页上处理（审批或回答），turn %s]\n" % turn_id
            )
        elif status == "failed":
            detail = snapshot.get("error") or {}
            out.write("\n[本轮失败] %s\n" % json.dumps(detail, ensure_ascii=False))
        return snapshot

    def _await_turn(self, turn_id, out):
        printed = {}
        seen = set()
        reconnects = 0
        while True:
            snapshot = None
            try:
                for frame in self.client.sse(self.session_id, self.cursor):
                    event_id = frame.get("id")
                    data = frame.get("data") or {}
                    type_ = str(data.get("type") or frame.get("event") or "")
                    if (
                        data.get("turn_id") == turn_id
                        and type_ == "agent.session.turn.output_text.delta"
                    ):
                        eid = str(data.get("event_id") or "")
                        if eid and eid in seen:
                            continue
                        seen.add(eid)
                        item_id = str(data.get("item_id"))
                        delta = str(data.get("delta") or "")
                        printed[item_id] = printed.get(item_id, "") + delta
                        out.write(delta)
                        out.flush()
                    reconcile = type_ == "yuxi.session.resync" or (
                        data.get("turn_id") == turn_id and type_ in TERMINAL_EVENTS
                    )
                    if reconcile:
                        snapshot = self.client.get_turn(self.session_id, turn_id)
                        if _is_settled(snapshot):
                            if event_id:
                                self.cursor = event_id
                            break
                    if event_id:
                        self.cursor = event_id
            except YuxiError as exc:
                out.write("\n[事件流中断：%s]\n" % exc)
            except OSError as exc:
                out.write("\n[事件流中断：%s]\n" % exc)

            if not (snapshot and _is_settled(snapshot)):
                snapshot = self.client.get_turn(self.session_id, turn_id)
            if _is_settled(snapshot):
                self._flush_missing(snapshot, printed, out)
                return snapshot
            if reconnects >= 3:
                raise YuxiError(
                    "turn %s 仍未结束，可稍后用 GET .../turns/%s 查询" % (turn_id, turn_id)
                )
            time.sleep(0.25 * (2 ** reconnects))
            reconnects += 1

    def _flush_missing(self, snapshot, printed, out):
        text = _turn_text(snapshot)
        if not text:
            return
        streamed = "".join(printed.values())
        if text.startswith(streamed):
            out.write(text[len(streamed):])
        else:
            out.write("\n" + text + "\n")
        out.flush()


class ClientApp:
    """REPL：管理会话、待发文件与发送。"""

    def __init__(self, client, agent_id, model):
        self.client = client
        self.agent_id = agent_id
        self.model = model
        self.session = None
        self.pending = []  # 已上传、尚未随消息提交的 draft id

    def ensure_session(self):
        if self.session is None:
            data, key = self.client.create_session(self.agent_id, self.model)
            self.session = ChatSession(self.client, data["id"])
            print("会话已创建：%s（模型 %s）" % (data["id"], self.model or "系统默认"))
            print("幂等键：%s" % key)
        return self.session

    def send(self, text):
        session = self.ensure_session()
        snapshot = session.send(text, list(self.pending))
        self.pending = []
        return snapshot

    def upload(self, path):
        file_id = self.client.upload_file(path)
        self.pending.append(file_id)
        print("已上传草稿 %s（%s），下一条消息会带上它" % (file_id, path))
        return file_id

    def run(self):
        print("输入文字直接发送；/help 看全部命令。")
        while True:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not line:
                continue
            if line in ("/exit", "/quit"):
                return
            if line == "/help":
                self._help()
                continue
            if line == "/new":
                self.session = None
                self.pending = []
                print("已重置；下一条消息会开新会话。")
                continue
            if line == "/turns":
                self._turns()
                continue
            if line == "/agents":
                self._agents()
                continue
            if line.startswith("/use"):
                parts = line.split(None, 1)
                if len(parts) < 2:
                    print("用法：/use <agent-slug>")
                    continue
                self.agent_id = parts[1].strip()
                self.session = None
                self.pending = []
                print("已切换到 agent %s；下一条消息会开新会话。" % self.agent_id)
                continue
            if line.startswith("/file"):
                self._file(line)
                continue
            try:
                self.send(line)
            except YuxiError as exc:
                print("失败：%s" % exc)
            print()

    def _file(self, line):
        parts = line.split(None, 2)
        if len(parts) < 2:
            print("用法：/file <路径> [要一起发送的话]")
            return
        path = parts[1]
        message = parts[2] if len(parts) > 2 else ""
        try:
            self.upload(path)
            if message:
                self.send(message)
                print()
        except YuxiError as exc:
            print("失败：%s" % exc)

    def _turns(self):
        if self.session is None:
            print("还没有会话。")
            return
        try:
            data = self.client.list_turns(self.session.session_id)
        except YuxiError as exc:
            print("失败：%s" % exc)
            return
        for item in data.get("data") or []:
            print(
                "%s  %-16s %s"
                % (
                    item.get("id"),
                    item.get("status"),
                    str(item.get("error") or "")[:120],
                )
            )

    def _agents(self):
        try:
            data = self.client.list_agents()
        except YuxiError as exc:
            print("失败：%s" % exc)
            return
        items = data.get("data") or []
        if not items:
            print("没有可用的 Agent（GET /api/v1/agents 返回空）。")
            return
        print("可指定的 Agent（用 /use <slug> 切换）：")
        for item in items:
            mark = "*" if item.get("id") == self.agent_id else " "
            print(
                "%s %s  %s"
                % (
                    mark,
                    _pad(str(item.get("id")), 24),
                    item.get("name") or item.get("description") or "",
                )
            )

    def _help(self):
        print(
            """
命令：
  <文字>              发送一条消息（带上待发文件）
  /file <路径>         上传文件草稿，不立即发送
  /file <路径> <文字>  上传文件并立刻发送
  /agents              列出可指定的 Agent
  /use <slug>          切换 Agent（下一条消息开新会话）
  /new                 放弃当前会话，下一条消息开新会话
  /turns               查看本会话的轮次与状态
  /exit                退出
"""
        )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Yuxi Public v1 API 本地客户端",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--base", default=os.getenv("BASE", "http://127.0.0.1:5050"))
    parser.add_argument("--agent", default=os.getenv("AGENT_SLUG", "default-chatbot"))
    parser.add_argument("--model", default=os.getenv("SPEC_MODEL", ""))
    parser.add_argument("--token", default=os.getenv("TOKEN", ""))
    parser.add_argument(
        "--file", action="append", default=[], help="起始文件，可重复指定"
    )
    args = parser.parse_args(argv)

    if not args.token:
        print("缺少 TOKEN：export TOKEN=... 或用 --token 指定", file=sys.stderr)
        return 2
    if not args.model:
        print("缺少 SPEC_MODEL：export SPEC_MODEL=provider_id:model_id", file=sys.stderr)
        return 2

    client = Client(args.base, args.token)
    try:
        health = client.health()
    except YuxiError as exc:
        print("后端不可用：%s" % exc, file=sys.stderr)
        return 1
    print("后端 %s 可用（版本 %s）" % (args.base, health.get("version") or "?"))

    app = ClientApp(client, args.agent, args.model)
    for path in args.file:
        try:
            app.upload(path)
        except YuxiError as exc:
            print("失败：%s" % exc)
            return 1

    try:
        app.run()
    except KeyboardInterrupt:
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
