from __future__ import annotations

import asyncio
import re
import time
import uuid
from typing import Any

from server import admin_store, chat_store, db
from server.chat import stream_chat, summarize_title
from server.mcp_client import McpError, OutlineMcpClient, normalize_mcp_url

_NEW_CHAT = re.compile(r"^(新会话|/new|/reset|新建会话|重新开始)\s*$", re.I)
_task: asyncio.Task[None] | None = None
_client: Any = None
_started_key = ""
_running = False
_error = ""
_seen_msg: dict[str, float] = {}


def _patch_botpy_group_message() -> None:
    """qq-botpy 1.2.x 只有 GROUP_AT_MESSAGE_CREATE；平台现已推 GROUP_MESSAGE_CREATE。"""
    try:
        from botpy.connection import ConnectionState
        from botpy.message import GroupMessage
    except ImportError:
        return
    if hasattr(ConnectionState, "parse_group_message_create"):
        return

    def parse_group_message_create(self, payload):
        _message = GroupMessage(self.api, payload.get("id", None), payload.get("d", {}))
        self._dispatch("group_message_create", _message)

    ConnectionState.parse_group_message_create = parse_group_message_create


def runtime_status() -> dict[str, Any]:
    return {"running": _running, "error": _error}


def public_status() -> dict[str, Any]:
    return {**admin_store.qq_public(), **runtime_status()}


async def sync() -> dict[str, Any]:
    cfg = admin_store.qq_config()
    app_id = str(cfg.get("app_id") or "").strip()
    secret = str(cfg.get("app_secret") or "").strip()
    enabled = bool(cfg.get("enabled"))
    key = f"{int(enabled)}:{app_id}:{secret}"
    global _started_key
    if not enabled:
        await stop()
        return public_status()
    if not app_id or not secret:
        await stop()
        _set_error("请先填写 AppID 和 AppSecret")
        return public_status()
    if key == _started_key and _task and not _task.done() and _running:
        return public_status()
    await stop()
    await start(app_id, secret)
    _started_key = key
    return public_status()


async def start(app_id: str, secret: str) -> None:
    global _task, _client, _running, _error
    try:
        import botpy
        from botpy.message import C2CMessage, GroupMessage
    except ImportError:
        _set_error("未安装 qq-botpy，请执行 pip install qq-botpy")
        return

    _patch_botpy_group_message()

    class WikiClient(botpy.Client):
        async def on_ready(self):
            global _running, _error
            _running = True
            _error = ""
            name = getattr(getattr(self, "robot", None), "name", "") or "QQ 机器人"
            print(f"[qq] 已连接: {name}")

        async def on_group_at_message_create(self, message: GroupMessage):
            await _handle_message("group", message)

        async def on_group_message_create(self, message: GroupMessage):
            await _handle_message("group", message)

        async def on_c2c_message_create(self, message: C2CMessage):
            await _handle_message("c2c", message)

    intents = botpy.Intents(public_messages=True)
    log_dir = db.DATA / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = dict(botpy.logging.DEFAULT_FILE_HANDLER,
                   filename=str(log_dir / '%(name)s.log'))
    client = WikiClient(intents=intents, ext_handlers=handler)
    _client = client
    _running = False
    _error = ""

    async def runner() -> None:
        global _running, _error
        try:
            try:
                await client.start(appid=app_id, secret=secret)
            except TypeError:
                await client.start(app_id, secret)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _running = False
            _error = str(exc)
            print(f"[qq] 连接失败: {exc}")
        finally:
            if _client is client:
                _running = False

    _task = asyncio.create_task(runner(), name="qq-bot")


async def stop() -> None:
    global _task, _client, _running, _started_key, _error
    task, client = _task, _client
    _task = None
    _client = None
    _started_key = ""
    _running = False
    if client is not None:
        close = getattr(client, "close", None)
        if callable(close):
            try:
                result = close()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass
    if task is not None:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    if not _error or _error.startswith("请先填写"):
        _error = ""


def _set_error(message: str) -> None:
    global _error, _running
    _error = message
    _running = False


def _duplicate_message(message: Any) -> bool:
    mid = str(getattr(message, "id", "") or "")
    if not mid:
        return False
    now = time.time()
    for key, seen_at in list(_seen_msg.items()):
        if now - seen_at > 90:
            _seen_msg.pop(key, None)
    if mid in _seen_msg:
        return True
    _seen_msg[mid] = now
    return False


async def _handle_message(kind: str, message: Any) -> None:
    if _duplicate_message(message):
        return
    text = _message_text(message)
    visitor = _visitor_id(kind, message)
    if _NEW_CHAT.match(text):
        _new_session(visitor)
        await _reply(kind, message, "已新建会话，直接提问即可。")
        return
    if not text:
        await _reply(kind, message, "请发送文字问题。")
        return
    try:
        reply = await _chat_turn(visitor, text)
    except Exception as exc:
        reply = str(exc) or "对话失败"
    await _reply(kind, message, reply)


def _message_text(message: Any) -> str:
    raw = str(getattr(message, "content", "") or "")
    raw = re.sub(r"<@!?\w+>", "", raw)
    return raw.strip()


def _visitor_id(kind: str, message: Any) -> str:
    author = getattr(message, "author", None)
    if kind == "group":
        group = str(getattr(message, "group_openid", "") or "")
        member = str(getattr(author, "member_openid", "") or getattr(author, "user_openid", "") or "")
        return f"qq:group:{group}:{member}"
    user = str(getattr(author, "user_openid", "") or getattr(author, "member_openid", "") or "")
    return f"qq:c2c:{user}"


def _new_session(visitor: str) -> dict[str, Any]:
    packed = chat_store.load_visitor_chats(visitor)
    sessions = [s for s in packed.get("sessions") or [] if isinstance(s, dict)]
    rec = {
        "id": str(uuid.uuid4()),
        "title": "新会话",
        "messages": [],
        "updatedAt": int(time.time() * 1000),
        "userTheme": {},
    }
    sessions.insert(0, rec)
    chat_store.save_visitor_chats(visitor, sessions, rec["id"])
    return rec


def _current_session(visitor: str) -> dict[str, Any]:
    packed = chat_store.load_visitor_chats(visitor)
    sessions = [s for s in packed.get("sessions") or [] if isinstance(s, dict)]
    current_id = packed.get("currentId") or ""
    for rec in sessions:
        if str(rec.get("id") or "") == current_id:
            return rec
    if sessions:
        return sessions[0]
    return _new_session(visitor)


def _save_session(visitor: str, rec: dict[str, Any]) -> None:
    packed = chat_store.load_visitor_chats(visitor)
    sessions = [s for s in packed.get("sessions") or [] if isinstance(s, dict)]
    found = False
    for i, item in enumerate(sessions):
        if str(item.get("id") or "") == rec["id"]:
            sessions[i] = rec
            found = True
            break
    if not found:
        sessions.insert(0, rec)
    rec["updatedAt"] = int(time.time() * 1000)
    chat_store.save_visitor_chats(visitor, sessions, rec["id"])


async def _chat_turn(visitor: str, text: str) -> str:
    rec = _current_session(visitor)
    messages = list(rec.get("messages") or [])
    first_round = not any(m.get("role") == "assistant" for m in messages if isinstance(m, dict))
    messages.append({"role": "user", "content": text})
    answer = await _complete_chat(messages)
    messages.append({"role": "assistant", "content": answer})
    rec["messages"] = messages
    if first_round:
        rec["title"] = await _title_for(messages) or rec.get("title") or "新会话"
    _save_session(visitor, rec)
    return _qq_plain(answer)


async def _complete_chat(messages: list[dict[str, Any]]) -> str:
    openai_base, openai_key, model, provider = admin_store.chat_credentials()
    if not openai_base or not openai_key or not model:
        raise RuntimeError("尚未配置模型接口")
    raw_mcp = admin_store.mcp_url()
    token = admin_store.mcp_oauth_token()
    if not raw_mcp:
        raise RuntimeError("尚未配置网协 MCP 地址")
    if not token:
        raise RuntimeError("尚未登录知识库，请在管理员界面点击「网协认证登陆」")
    mcp_url = normalize_mcp_url(raw_mcp)

    async def run(tok: str) -> str:
        chunks: list[str] = []
        async with OutlineMcpClient(mcp_url, tok) as mcp:
            tools = await mcp.list_tools() if admin_store.mcp_heat() > 0 else []
            async for event in stream_chat(
                openai_base=openai_base,
                openai_key=openai_key,
                model=model,
                messages=messages,
                mcp=mcp,
                tools=tools,
                provider=provider,
                system_prompt=admin_store.system_prompt(),
                mcp_heat=admin_store.mcp_heat(),
            ):
                kind = event.get("type")
                if kind == "delta" and event.get("text"):
                    chunks.append(str(event["text"]))
                elif kind == "error":
                    raise RuntimeError(str(event.get("message") or "对话失败"))
                elif kind == "done" and event.get("content"):
                    return str(event["content"])
        return "".join(chunks).strip() or "（模型没有返回文本）"

    try:
        return await run(token)
    except McpError as exc:
        if exc.status != 401:
            raise
        token = await admin_store.refresh_mcp_oauth()
        return await run(token)


async def _title_for(messages: list[dict[str, Any]]) -> str:
    openai_base, openai_key, model, provider = admin_store.chat_credentials()
    try:
        return await summarize_title(
            openai_base=openai_base,
            openai_key=openai_key,
            model=model,
            messages=messages,
            provider=provider,
        )
    except Exception:
        return ""


def _qq_plain(text: str) -> str:
    raw = str(text or "")
    raw = re.sub(r"```[\s\S]*?```", lambda m: m.group(0).replace("```", "").strip(), raw)
    raw = re.sub(r"`([^`]+)`", r"\1", raw)
    raw = re.sub(r"\*\*([^*]+)\*\*", r"\1", raw)
    raw = re.sub(r"^#{1,6}\s*", "", raw, flags=re.M)
    raw = re.sub(r"\[([^\]]+)\]\((https?:[^)\s]+)\)", r"\1 \2", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()[:1800] or "（没有可发送的文本）"


async def _reply(kind: str, message: Any, text: str) -> None:
    api = getattr(message, "_api", None)
    if api is None:
        return
    body = _qq_plain(text)
    try:
        if kind == "group":
            await api.post_group_message(
                group_openid=message.group_openid,
                msg_type=0,
                msg_id=message.id,
                content=body,
            )
        else:
            openid = getattr(message.author, "user_openid", None) or getattr(message.author, "id", "")
            await api.post_c2c_message(
                openid=openid,
                msg_type=0,
                msg_id=message.id,
                content=body,
            )
    except Exception as exc:
        print(f"[qq] 回复失败: {exc}")
