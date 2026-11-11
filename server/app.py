from __future__ import annotations

import json
import asyncio
from contextlib import suppress
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer
from starlette.middleware.sessions import SessionMiddleware

from server import attendance_live, checkin_access, session_policy, admin_store, chat_store, data_admin, db, duty_settings, field_duty, log_rotation, mailer, operation_log, overview, permissions, push_reports, root_admin, schedule_store, swap_store, system_clock, weekly_store, member_store, security
from server.mcp_client import McpError, OutlineMcpClient, normalize_mcp_url
from server.oauth import exchange_code, start_oauth

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DATA = ROOT / "data"
ICON = ROOT / "icon"
DEFAULT_MCP = ""


def _session_secret() -> str:
    env = os.environ.get("SESSION_SECRET")
    if env:
        return env
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / "session_secret.txt"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    secret = secrets.token_hex(32)
    path.write_text(secret, encoding="utf-8")
    return secret


def _qq_bot():
    """Load the optional QQ bridge lazily: it pulls in asyncio helpers we otherwise skip."""
    from server import qq_bot
    return qq_bot


def _chat_helpers():
    from server import chat
    return chat


async def _log_rotation_worker():
    """Keep data/logs and the operation log within their size cap.

    Checking file sizes is cheap, but there is no reason to do it on every request, so
    it runs every five minutes alongside the other background workers.
    """
    while True:
        try:
            log_rotation.prune()
            operation_log.prune()
        except Exception as exc:
            print(f'[log] 日志滚动暂时失败：{type(exc).__name__}')
        await asyncio.sleep(300)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    admin_store.load()
    permissions.ensure_defaults()
    system_clock.load()
    with db.read() as conn:
        operation_log.init(conn)
    result = member_store.provision()
    print(f"[members] 新建普通用户账号 {result['created']} 个，保留现有账号 {result['existing']} 个")
    slug = admin_store.path()
    # An installed site that predates the root account gets one on this boot.
    if admin_store.has_user() and not root_admin.exists():
        generated = root_admin.bootstrap_existing()
        if generated:
            print("[root] 已创建原始管理员；一次性初始密码：" + generated)
            print("[root] 请用它登录后在「其他设置 → 原始管理员密码」中修改。")
    print("")
    print(f"管理员界面: http://127.0.0.1:50025/{slug}")
    if not admin_store.has_user():
        print("尚未注册管理员，首次打开站点会跳转到该地址并创建账号。")
    print("")
    if admin_store.qq_config().get('enabled'):
        await _qq_bot().sync()
    push_task = asyncio.create_task(push_reports.worker())
    presence_task = asyncio.create_task(session_policy.presence_worker())
    log_task = asyncio.create_task(_log_rotation_worker())
    try:
        yield
    finally:
        for task in (push_task, presence_task, log_task):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    if admin_store.qq_config().get('enabled'):
        await _qq_bot().stop()


class NoCacheStatic(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        # Old scripts must never survive a deploy; browsers revalidate every static file.
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response


app = FastAPI(title="网络服务协会 排班签到", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret(),
    session_cookie="gdutnic_wiki",
    same_site="lax",
    # HTTPS is required whenever the app is served over TLS; plain HTTP on a LAN stays usable.
    https_only=os.environ.get("SESSION_HTTPS_ONLY", "").lower() in {"1", "true", "yes"},
    max_age=security.SESSION_TTL,
)

ADMIN_LIMITER = security.AttemptLimiter()
ACCOUNT_LIMITER = security.AttemptLimiter()
# Duty-account logins are throttled per source address as well; without it the login
# form is an unthrottled password oracle for every member account.
MEMBER_LIMITER = security.AttemptLimiter(max_attempts=security.MEMBER_MAX_ATTEMPTS,
                                         window=security.MEMBER_WINDOW_SECONDS)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path in {'/api/attendance/live', '/api/session/heartbeat'}:
        # These read-only requests may finish after a logout/login in another tab.
        # Do not let their old session snapshot replace the browser's newer cookie.
        response.raw_headers = [(key, value) for key, value in response.raw_headers
                                if not (key.lower() == b'set-cookie' and value.startswith(b'gdutnic_wiki='))]

    # Credentials must never leak through a referrer or be framed for clickjacking.
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline';"
        " script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none';"
        " form-action 'self'",
    )
    if os.environ.get("SESSION_HTTPS_ONLY", "").lower() in {"1", "true", "yes"}:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


VISITOR_COOKIE = "gdutnic_visitor"
ADMIN_COOKIE = "gdutnic_admin"


def _admin_signer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_session_secret(), salt="gdutnic-admin")


def _set_admin_cookie(response: JSONResponse, username: str, session_id: str, *, version: str | None = None) -> None:
    token = _admin_signer().dumps({"u": username, "v": version if version is not None else member_store.session_version(username), "sid": session_id})
    response.set_cookie(
        ADMIN_COOKIE,
        token,
        max_age=security.SESSION_TTL,
        httponly=True,
        samesite="lax",
        secure=os.environ.get("SESSION_HTTPS_ONLY", "").lower() in {"1", "true", "yes"},
        path="/",
    )


def _clear_admin_cookie(response: JSONResponse) -> None:
    response.delete_cookie(ADMIN_COOKIE, path="/")


def _admin_from_cookie(request: Request) -> str:
    raw = request.cookies.get(ADMIN_COOKIE) or ""
    if not raw:
        return ""
    try:
        data = _admin_signer().loads(raw, max_age=security.SESSION_TTL)
    except (BadSignature, Exception):
        return ""
    name = str(data.get("u") or "").strip()
    if (name and member_store.session_valid(name, data.get("v"), use_cache=False)
            and session_policy.state(data.get("sid"), "admin", name)):
        request.state.admin_session_id = data["sid"]
        request.state.admin_session_version = data["v"]
        return name
    return ""


def _visitor_id(request: Request) -> str:
    value = (request.cookies.get(VISITOR_COOKIE) or "").strip()
    if len(value) >= 16:
        return value
    return secrets.token_urlsafe(18)


def _with_visitor_cookie(request: Request, payload: dict[str, Any], visitor: str) -> JSONResponse:
    response = JSONResponse(payload)
    if request.cookies.get(VISITOR_COOKIE) != visitor:
        response.set_cookie(
            VISITOR_COOKIE,
            visitor,
            max_age=60 * 60 * 24 * 400,
            httponly=True,
            samesite="lax",
            path="/",
        )
    return response


app.mount("/static", NoCacheStatic(directory=WEB), name="static")
ICON.mkdir(parents=True, exist_ok=True)
app.mount("/icon", NoCacheStatic(directory=ICON), name="icon")


@app.get("/favicon.ico")
async def favicon():
    path = ICON / "gdutnic.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail="缺少 icon/gdutnic.png")
    return FileResponse(path, media_type="image/png")


@app.get("/")
async def index():
    if not admin_store.has_user():
        return RedirectResponse(f"/{admin_store.path()}", status_code=302)
    return FileResponse(
        WEB / "index.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


def _resolve_mcp_url(request: Request, body: dict[str, Any] | None = None) -> str:
    # Only the administrator-controlled destination is trusted. Old signed
    # sessions may contain URLs originally supplied by anonymous clients.
    return admin_store.mcp_url()


def _require_mcp_user(request: Request) -> None:
    if not _admin_ok(request):
        _require_member(request)


@app.get("/api/defaults")
async def defaults(request: Request) -> dict[str, Any]:
    session = request.session
    return {
        "mcp_url": admin_store.mcp_url() or "",
        "has_outline_token": bool(session.get("outline_token") or session.get("mcp_api_key")),
        "oauth_connected": bool(session.get("outline_token")),
    }


@app.post("/api/settings")
async def save_settings(request: Request) -> dict[str, Any]:
    _require_mcp_user(request)
    body = await request.json()
    raw = _resolve_mcp_url(request)
    if raw:
        request.session["mcp_url"] = normalize_mcp_url(raw)
    api_key = (body.get("mcp_api_key") or "").strip()
    if api_key:
        request.session["mcp_api_key"] = api_key
    if body.get("clear_mcp_api_key"):
        request.session.pop("mcp_api_key", None)
    return {"ok": True, "mcp_url": request.session.get("mcp_url", "")}


@app.post("/api/mcp/connect")
async def mcp_connect(request: Request) -> dict[str, Any]:
    _require_mcp_user(request)
    body = await request.json()
    raw = _resolve_mcp_url(request, body)
    if not raw:
        raise HTTPException(status_code=400, detail="请先在管理员界面填写网协 MCP 地址")
    mcp_url = normalize_mcp_url(raw)
    request.session["mcp_url"] = mcp_url
    token = _token_from(request, body)
    if not token:
        raise HTTPException(status_code=401, detail="尚未登录：请点击「企业登录」在弹窗中输入账号密码")
    try:
        async with OutlineMcpClient(mcp_url, token) as mcp:
            tools = await mcp.list_tools()
            return {
                "ok": True,
                "mcp_url": mcp.mcp_url,
                "server": mcp.server_info.get("serverInfo") or mcp.server_info,
                "tools": [
                    {"name": t.get("name"), "description": t.get("description") or ""}
                    for t in tools
                ],
            }
    except McpError as exc:
        print(f"[mcp_connect] {exc} payload={exc.payload!r}")
        status = 401 if exc.status == 401 else 502
        raise HTTPException(status_code=status, detail="MCP 连接失败，请检查配置或重新登录") from exc
    except Exception as exc:
        print(f"[mcp_connect] {type(exc).__name__}: {exc}")
        raise HTTPException(status_code=502, detail="MCP 连接失败，请检查配置或重新登录") from exc


@app.get("/api/mcp/oauth/start")
async def oauth_start(request: Request, mcp_url: str | None = None):
    _require_mcp_user(request)
    raw = _resolve_mcp_url(request)
    if not raw:
        return _oauth_popup_result("missing_url")
    mcp_url = normalize_mcp_url(raw)
    redirect_uri = str(request.base_url).rstrip("/") + str(request.url_for("oauth_callback").path)
    try:
        flow = await start_oauth(mcp_url, redirect_uri)
    except Exception as exc:
        print(f"[oauth_start] {type(exc).__name__}: {exc}")
        return _oauth_popup_result("start", "企业登录暂时不可用")
    flow["redirect_uri"] = redirect_uri
    request.session["oauth_flow"] = flow
    request.session["mcp_url"] = mcp_url
    return RedirectResponse(flow["authorize_url"], status_code=302)


@app.get("/api/mcp/oauth/callback")
async def oauth_callback(request: Request, code: str | None = None, state: str | None = None) -> HTMLResponse:
    _require_mcp_user(request)
    flow = request.session.get("oauth_flow") or {}
    if not code or not flow:
        return _oauth_popup_result("missing")
    if state != flow.get("state"):
        return _oauth_popup_result("state")
    try:
        tokens = await exchange_code(
            token_endpoint=flow["token_endpoint"],
            client_id=flow["client_id"],
            client_secret=flow.get("client_secret") or "",
            code=code,
            redirect_uri=flow.get("redirect_uri") or str(request.url_for("oauth_callback")),
            code_verifier=flow["code_verifier"],
            mcp_url=flow["mcp_url"],
        )
    except Exception as exc:
        return _oauth_popup_result("token", "企业登录暂时不可用")
    request.session["outline_token"] = (
        tokens.get("access_token")
        or (tokens.get("data") or {}).get("access_token")
        or ""
    )
    request.session["outline_refresh"] = tokens.get("refresh_token")
    request.session["oauth_meta"] = {
        "token_endpoint": flow["token_endpoint"],
        "client_id": flow["client_id"],
        "client_secret": flow.get("client_secret") or "",
        "mcp_url": flow["mcp_url"],
    }
    request.session.pop("oauth_flow", None)
    if _admin_ok(request) and request.session.get("outline_token"):
        admin_store.save_oauth(
            request.session.get("outline_token") or "",
            request.session.get("outline_refresh") or "",
            request.session.get("oauth_meta") or {},
        )
    return _oauth_popup_result(
        "ok",
        extra={
            "access_token": request.session.get("outline_token") or "",
            "refresh_token": request.session.get("outline_refresh") or "",
            "mcp_url": flow.get("mcp_url") or "",
        },
    )


@app.post("/api/mcp/restore")
async def mcp_restore(request: Request) -> dict[str, bool]:
    _require_mcp_user(request)
    body = await request.json()
    token = (body.get("outline_token") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="缺少登录凭证")
    request.session["outline_token"] = token
    refresh = (body.get("outline_refresh") or "").strip()
    if refresh:
        request.session["outline_refresh"] = refresh
    raw = _resolve_mcp_url(request)
    if raw:
        request.session["mcp_url"] = normalize_mcp_url(raw)
    return {"ok": True}


@app.post("/api/mcp/logout")
async def mcp_logout(request: Request) -> dict[str, bool]:
    for key in ("outline_token", "outline_refresh", "oauth_meta", "mcp_api_key", "oauth_flow"):
        request.session.pop(key, None)
    return {"ok": True}


@app.post("/api/chat")
async def chat(request: Request) -> StreamingResponse:
    _require_mcp_user(request)
    body = await request.json()
    raw = _resolve_mcp_url(request, body)
    if not raw:
        raise HTTPException(status_code=400, detail="请先在管理员界面填写网协 MCP 地址")
    mcp_url = normalize_mcp_url(raw)
    request.session["mcp_url"] = mcp_url
    token = _token_from(request, body)
    if not token:
        return JSONResponse({"error": "尚未连接 Outline MCP，请先在弹窗中完成企业登录"}, status_code=401)

    openai_base, openai_key, model, provider = admin_store.chat_credentials()
    messages = []
    for item in body.get("messages") or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        content = chat_store._trim_content(item.get("content"))
        if content in ("", []):
            continue
        messages.append({"role": role, "content": content})
    if not openai_base or not openai_key or not model:
        raise HTTPException(status_code=400, detail="尚未配置模型接口，请先打开管理员界面完成设置")
    if not messages:
        raise HTTPException(status_code=400, detail="消息不能为空")

    async def events():
        try:
            async with OutlineMcpClient(mcp_url, token) as mcp:
                tools = await mcp.list_tools() if admin_store.mcp_heat() > 0 else []
                yield _sse({"type": "mcp_ready", "tools": [t.get("name") for t in tools]})
                async for event in _chat_helpers().stream_chat(
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
                    yield _sse(event)
        except McpError as exc:
            yield _sse({"type": "error", "message": "服务请求失败，请稍后重试"})
        except Exception as exc:
            yield _sse({"type": "error", "message": "服务请求失败，请稍后重试"})

    return StreamingResponse(events(), media_type="text/event-stream")


@app.post("/api/chat/title")
async def chat_title(request: Request) -> dict[str, str]:
    body = await request.json()
    openai_base, openai_key, model, provider = admin_store.chat_credentials()
    if not openai_base or not openai_key or not model:
        raise HTTPException(status_code=400, detail="尚未配置模型接口")
    messages = []
    for item in body.get("messages") or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        content = chat_store._trim_content(item.get("content"))
        if isinstance(content, list):
            texts = [
                part
                for part in content
                if isinstance(part, dict) and part.get("type") == "text" and str(part.get("text") or "").strip()
            ]
            content = texts
        if role != "assistant":
            continue
        if content in ("", []):
            continue
        messages.append({"role": "assistant", "content": content})
        if len(messages) >= 2:
            break
    if not messages:
        raise HTTPException(status_code=400, detail="消息不能为空")
    try:
        title = await _chat_helpers().summarize_title(
            openai_base=openai_base,
            openai_key=openai_key,
            model=model,
            messages=messages,
            provider=provider,
        )
    except Exception:
        title = ""
    return {"title": title}


def _token_from(request: Request, body: dict[str, Any]) -> str:
    from_body = (body.get("mcp_api_key") or "").strip()
    if from_body:
        request.session["mcp_api_key"] = from_body
        return from_body
    return (
        request.session.get("mcp_api_key")
        or request.session.get("outline_token")
        or ""
    ).strip()


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _oauth_popup_result(status: str, message: str = "", extra: dict[str, Any] | None = None) -> HTMLResponse:
    payload = json.dumps(
        {"type": "outline-oauth", "status": status, "message": message, **(extra or {})},
        ensure_ascii=False,
    )
    ok = status == "ok"
    detail = message or ("登录成功，正在关闭窗口…" if ok else "登录未完成，请关闭此窗口后重试。")
    auto_close = "window.close();" if ok else ""
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
  <head><meta charset="utf-8"><title>知识库登录</title></head>
  <body style="font-family:sans-serif;padding:24px;max-width:420px">
    <p>{detail}</p>
    <script>
      const payload = {payload};
      try {{
        if (payload.status === "ok" && payload.access_token) {{
          localStorage.setItem("gdutnic-wiki-auth", JSON.stringify({{
            access_token: payload.access_token,
            refresh_token: payload.refresh_token || "",
            mcp_url: payload.mcp_url || ""
          }}));
        }}
      }} catch (e) {{}}
      try {{
        if (window.opener) {{
          window.opener.postMessage(payload, window.location.origin);
        }}
      }} catch (e) {{}}
      {auto_close}
      setTimeout(function () {{
        if (!window.closed && payload.status === "ok") {{
          location.replace("/?oauth=" + encodeURIComponent(payload.status));
        }}
      }}, 400);
    </script>
  </body>
</html>"""
    return HTMLResponse(html)


def _admin_gate(request: Request) -> bool:
    return request.session.get("admin_gate") == admin_store.path()


def _log(actor, action, detail='', role='admin'):
    """Record an operation; the actor is whatever name the session belonged to."""
    operation_log.record(actor, action, detail, role)


def _shift_summary(body):
    """Human-readable description of a shift payload."""
    names = body.get('members') if isinstance(body, dict) else None
    people = '、'.join(str(n) for n in names) if isinstance(names, list) else ''
    if not isinstance(body, dict):
        return ''
    return (f"{body.get('day', '')} {body.get('start', '')}–{body.get('end', '')}"
            + (f"（{people}）" if people else ''))


def _session_account(request: Request) -> str:
    """The account behind this admin session, from the signed cookie or the session."""
    name = _admin_from_cookie(request)
    if name:
        return name if permissions.is_admin(name) else ""
    if not request.session.get("admin_ok"):
        return ""
    name = str(request.session.get("admin_username") or "").strip()
    version = request.session.get("admin_version")
    if (name and member_store.session_valid(name, version, use_cache=False)
            and session_policy.state(request.session.get("admin_sid"), "admin", name)
            and permissions.is_admin(name)):
        request.state.admin_session_id = request.session["admin_sid"]
        request.state.admin_session_version = version
        return name
    return ""



def _admin_ok(request: Request) -> bool:
    return bool(_session_account(request))


def _require_admin(request: Request) -> None:
    if not _admin_ok(request):
        raise HTTPException(status_code=401, detail="请先登录管理员")


def _require_permission(request: Request, permission: str) -> str:
    """Administrator endpoints are gated per permission group."""
    name = _session_account(request)
    if not name:
        raise HTTPException(status_code=401, detail="请先登录管理员")
    # Legacy model/MCP settings routes retain their original authorization even
    # though the settings sidebar now also contains SSO and network controls. The
    # credentials they expose are masked on read, so opening the page with one card
    # cannot reveal another card's secrets.
    allowed = (any(permissions.has(name, key) for key in ('rules', 'clock', 'smtp'))
               if permission == 'settings' else permissions.has(name, permission))
    if not allowed:
        raise HTTPException(status_code=403, detail="当前账号没有此权限")
    return name


def _reject_root(name: str) -> None:
    """The 原始管理员 is managed only through its own file, never these endpoints."""
    if root_admin.is_root(name):
        raise HTTPException(status_code=403, detail="原始管理员不可在此修改，只能修改其密码")


def _may_manage(target: str, actor: str) -> bool:
    """Without the grant permission, an account cannot touch a privileged account.

    A plain duty account (only the user permission) is fair game; anyone holding any
    administrative permission is protected from accounts that cannot grant permissions.
    """
    if target == actor:
        return True
    if permissions.has(actor, "members"):
        return True
    return not permissions.is_admin(target)


def _admin_username(request: Request) -> str:
    name = _admin_from_cookie(request)
    if name:
        return name
    if not _admin_ok(request):
        return ""
    name = str(request.session.get("admin_username") or "").strip()
    if name:
        return name
    names = admin_store.usernames()
    if names:
        request.session["admin_username"] = names[0]
        return names[0]
    return ""


@app.get("/api/admin/status")
async def admin_status(request: Request) -> dict[str, Any]:
    name = _session_account(request)
    return {
        "setup": admin_store.has_user(),
        "logged_in": bool(name),
        "root": bool(name and root_admin.is_root(name)),
        "gate": _admin_gate(request),
        "username": name or _admin_username(request) if name else "",
        "permissions": sorted(permissions.permissions_of(name)) if name else [],
        "now": system_clock.public(),
        "can_grant": bool(name and permissions.has(name, "accounts_protected")),
    }


@app.post("/api/admin/register")
async def admin_register(request: Request) -> dict[str, Any]:
    if not _admin_gate(request):
        raise HTTPException(status_code=403, detail="请从控制台提示的管理员网址进入")
    # The real guard is the root credential file, not the `setup_done` setting: that row
    # also lives in the database and must never be able to reopen registration.
    if root_admin.exists() or admin_store.has_user():
        raise HTTPException(status_code=409, detail="管理员已注册，请直接登录")
    body = await request.json()
    # The first account is the 原始管理员. Its name may be chosen here (空白时用内置名称)
    # and its credentials live in data/root_admin.json rather than the database.
    password = str(body.get("password") or "")
    try:
        if len(password) < 6:
            raise ValueError("密码至少 6 位")
        root_admin.create_if_missing(password, body.get("username"))
        admin_store.remember_setup(root_admin.username())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    username = root_admin.username()
    request.session["admin_ok"] = True
    request.session["admin_username"] = username
    request.session["admin_version"] = member_store.session_version(username)
    request.session["admin_sid"] = session_policy.create("admin", username)
    _log(username, '创建原始管理员', '首次注册，凭据保存于 data/root_admin.json')
    response = JSONResponse({"ok": True, "username": username})
    _set_admin_cookie(response, username, request.session["admin_sid"])
    return response


@app.post("/api/admin/login")
async def admin_login(request: Request) -> JSONResponse:
    if not _admin_gate(request):
        raise HTTPException(status_code=403, detail="请从控制台提示的管理员网址进入")
    key = "admin:" + security.client_key(request)
    if ADMIN_LIMITER.blocked(key):
        raise HTTPException(status_code=429, detail="管理员登录尝试过于频繁，请在限制开始 10 分钟后重试")
    body = await request.json()
    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    account_key = "admin:" + username.casefold()
    if ACCOUNT_LIMITER.blocked(account_key):
        raise HTTPException(status_code=429, detail="管理员登录尝试过于频繁，请在限制开始 10 分钟后重试")
    # The 原始管理员 authenticates against its own file, never the account table.
    if root_admin.is_root(username):
        if not root_admin.verify(password):
            ADMIN_LIMITER.fail(key)
            ACCOUNT_LIMITER.fail(account_key)
            raise HTTPException(status_code=401, detail="用户名或密码不正确")
        ADMIN_LIMITER.reset(key)
        ACCOUNT_LIMITER.reset(account_key)
        _log(username, '登录管理端')
        request.session["admin_ok"] = True
        request.session["admin_username"] = username
        request.session["admin_version"] = member_store.session_version(username)
        request.session["admin_sid"] = session_policy.create("admin", username)
        response = JSONResponse({"ok": True, "username": username})
        _set_admin_cookie(response, username, request.session["admin_sid"])
        return response
    version = member_store.authenticate(username, password)
    if not version:
        ADMIN_LIMITER.fail(key)
        ACCOUNT_LIMITER.fail(account_key)
        raise HTTPException(status_code=401, detail="用户名或密码不正确")
    # Administrator pages are open to any account with at least one admin permission.
    if not permissions.is_admin(username):
        ADMIN_LIMITER.fail(key)
        ACCOUNT_LIMITER.fail(account_key)
        raise HTTPException(status_code=403, detail="该账号没有管理员权限")
    ADMIN_LIMITER.reset(key)
    ACCOUNT_LIMITER.reset(account_key)
    _log(username, '登录管理端')
    request.session["admin_ok"] = True
    request.session["admin_username"] = username
    request.session["admin_version"] = member_store.session_version(username)
    request.session["admin_sid"] = session_policy.create("admin", username)
    response = JSONResponse({"ok": True, "username": username})
    _set_admin_cookie(response, username, request.session["admin_sid"])
    return response


@app.post("/api/admin/logout")
async def admin_logout(request: Request) -> JSONResponse:
    _log(_admin_username(request), '退出管理端')
    session_policy.revoke(getattr(request.state, 'admin_session_id', None))
    session_policy.revoke(request.session.pop('admin_sid', None))
    request.session.pop("admin_ok", None)
    request.session.pop("admin_username", None)
    request.session.pop("admin_version", None)
    response = JSONResponse({"ok": True})
    _clear_admin_cookie(response)
    return response


@app.get("/api/admin/users")
async def admin_list_users(request: Request) -> dict[str, Any]:
    _require_permission(request, "members")
    return {"usernames": admin_store.usernames()}


@app.post("/api/admin/users")
async def admin_add_user(request: Request) -> dict[str, Any]:
    _require_permission(request, "members")
    body = await request.json()
    try:
        admin_store.add_user(str(body.get("username") or ""), str(body.get("password") or ""))
        _log(_admin_username(request), '新增管理员账号', str(body.get("username") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "usernames": admin_store.usernames()}


@app.patch("/api/admin/users")
async def admin_update_user(request: Request) -> dict[str, Any]:
    _require_permission(request, "members")
    body = await request.json()
    old = str(body.get("old_username") or "").strip()
    if old and old == str(request.session.get("admin_username") or ""):
        raise HTTPException(status_code=400, detail="不能修改当前登录的管理员")
    try:
        name = admin_store.update_user(old, str(body.get("username") or ""), str(body.get("password") or ""))
        _log(_admin_username(request), '修改管理员账号', f'{old} → {name}')
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "username": name, "usernames": admin_store.usernames()}


@app.delete("/api/admin/users")
async def admin_delete_user(request: Request) -> dict[str, Any]:
    _require_permission(request, "members")
    body = await request.json()
    name = str(body.get("username") or "").strip()
    try:
        admin_store.delete_user(name, str(request.session.get("admin_username") or ""))
        _log(_admin_username(request), '删除管理员账号', name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "usernames": admin_store.usernames()}


@app.get("/api/admin/model")
async def admin_model_get(request: Request) -> dict[str, Any]:
    _require_permission(request, "settings")
    return admin_store.model_public()


@app.post("/api/admin/model")
async def admin_model_save(request: Request) -> dict[str, Any]:
    _require_permission(request, "settings")
    body = await request.json()
    return admin_store.save_model(body)


@app.get("/api/admin/mcp")
async def admin_mcp_get(request: Request) -> dict[str, Any]:
    _require_permission(request, "settings")
    return admin_store.mcp_public()


@app.post("/api/admin/mcp")
async def admin_mcp_save(request: Request) -> dict[str, Any]:
    _require_permission(request, "settings")
    body = await request.json()
    return admin_store.save_mcp(body)


@app.post("/api/admin/oauth")
async def admin_oauth_persist(request: Request) -> dict[str, Any]:
    _require_permission(request, "settings")
    token = str(request.session.get("outline_token") or "").strip()
    refresh = str(request.session.get("outline_refresh") or "").strip()
    meta = request.session.get("oauth_meta") if isinstance(request.session.get("oauth_meta"), dict) else {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    if isinstance(body, dict):
        token = str(body.get("access_token") or token).strip()
        refresh = str(body.get("refresh_token") or refresh).strip()
    if not token:
        raise HTTPException(status_code=400, detail="尚未完成知识库登录")
    if token:
        request.session["outline_token"] = token
    if refresh:
        request.session["outline_refresh"] = refresh
    return admin_store.save_oauth(token, refresh, meta)


@app.post("/api/admin/oauth/logout")
async def admin_oauth_logout(request: Request) -> dict[str, Any]:
    _require_permission(request, "settings")
    return admin_store.clear_oauth()


@app.get("/api/admin/smtp")
async def admin_smtp_get(request: Request) -> dict[str, Any]:
    _require_permission(request, "smtp")
    return admin_store.smtp_public()


@app.post("/api/admin/smtp")
async def admin_smtp_save(request: Request) -> dict[str, Any]:
    _require_permission(request, "smtp")
    body = await request.json()
    try:
        result = admin_store.save_smtp(body)
        _log(_admin_username(request), '修改 SMTP 设置',
             f"{result['host'] or '(未设置)'}:{result['port']} · "
             f"{'启用' if result['enabled'] else '停用'}")
        return result
    except ValueError as exc:
        raise _schedule_error(exc) from exc


@app.post("/api/admin/smtp/test")
async def admin_smtp_test(request: Request) -> dict[str, Any]:
    """Send one test message to an address the operator types; nothing is stored."""
    _require_permission(request, "smtp")
    body = await request.json()
    to = str(body.get("to") or "").strip()
    if not to:
        raise HTTPException(status_code=400, detail="请填写收件邮箱")
    try:
        result = mailer.send_test(to)
    except (RuntimeError, ValueError) as exc:
        _log(_admin_username(request), 'SMTP 测试失败', f"{to}：{exc}")
        raise _schedule_error(exc) from exc
    _log(_admin_username(request), 'SMTP 测试成功', to)
    return result


@app.get("/api/admin/qq")
async def admin_qq_get(request: Request) -> dict[str, Any]:
    _require_permission(request, "settings")
    return _qq_bot().public_status()


@app.post("/api/admin/qq")
async def admin_qq_save(request: Request) -> dict[str, Any]:
    _require_permission(request, "settings")
    body = await request.json()
    admin_store.save_qq(body)
    return await _qq_bot().sync()


@app.get("/api/chats")
async def chats_get(request: Request) -> JSONResponse:
    visitor = _visitor_id(request)
    packed = chat_store.load_visitor_chats(visitor)
    return _with_visitor_cookie(request, packed, visitor)


@app.put("/api/chats")
async def chats_put(request: Request) -> JSONResponse:
    visitor = _visitor_id(request)
    body = await request.json()
    sessions = body.get("sessions") if isinstance(body.get("sessions"), list) else []
    chat_store.save_visitor_chats(visitor, sessions, str(body.get("currentId") or ""))
    return _with_visitor_cookie(request, {"ok": True}, visitor)


@app.get("/api/admin/chats")
async def admin_chats_list(request: Request) -> dict[str, Any]:
    _require_permission(request, "data")
    return {"items": chat_store.list_all(), **chat_store.stats()}


@app.post("/api/admin/chats/cache")
async def admin_chats_cache(request: Request) -> dict[str, Any]:
    _require_permission(request, "data")
    body = await request.json()
    try:
        max_mb = int(body.get("max_mb"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="请填写有效的缓存大小") from None
    stats = chat_store.set_max_mb(max_mb)
    return {"ok": True, "items": chat_store.list_all(), **stats}


@app.get("/api/admin/chats/{visitor_id}/{chat_id}")
async def admin_chats_detail(request: Request, visitor_id: str, chat_id: str) -> dict[str, Any]:
    _require_permission(request, "data")
    rec = chat_store.get_session(visitor_id, chat_id)
    if not rec:
        raise HTTPException(status_code=404, detail="对话不存在")
    return rec


def _schedule_error(exc):
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc) or "排班数据格式不正确")


@app.get("/api/admin/logs")
async def admin_logs(request: Request, limit: int = 300, actor: str = "", q: str = ""):
    _require_permission(request, "logs")
    limit = max(1, min(int(limit or 300), 1000))
    return {"items": operation_log.list_entries(limit, actor.strip(), q.strip()),
            "actors": operation_log.actors(),
            "stats": operation_log.stats()}


@app.get("/api/admin/data/tables")
async def admin_data_tables(request: Request):
    """Table list for 数据管理; only the whitelisted tables are ever returned."""
    _require_permission(request, "data")
    return data_admin.list_tables()


@app.get("/api/admin/data/tables/{name}")
async def admin_data_preview(name: str, request: Request, page: int = 1, size: int = 50, q: str = ""):
    _require_permission(request, "data")
    try:
        return data_admin.preview(name, page, size, q, _admin_username(request))
    except LookupError as exc:
        raise _schedule_error(exc) from exc
    except (TypeError, ValueError) as exc:
        raise _schedule_error(exc) from exc


@app.put("/api/admin/data/tables/{name}")
async def admin_data_update(name: str, request: Request):
    _require_permission(request, "data")
    body = await request.json()
    try:
        result = data_admin.update_row(name, body.get("keys") or {}, body.get("values") or {},
                                       _admin_username(request))
    except (TypeError, ValueError, LookupError) as exc:
        raise _schedule_error(exc) from exc
    _log(_admin_username(request), '修改数据表',
         f"{name}：修改 {result['updated']} 行（{'、'.join(result['columns'])}）")
    return result


@app.post("/api/admin/data/tables/{name}/delete-many")
async def admin_data_delete_many(name: str, request: Request):
    """Delete the rows the administrator selected with click / shift-click."""
    _require_permission(request, "data")
    body = await request.json()
    keys = body.get("keys")
    if not isinstance(keys, list):
        raise HTTPException(status_code=400, detail="请提供要删除的记录")
    try:
        result = data_admin.delete_many(name, keys, _admin_username(request))
    except (TypeError, ValueError, LookupError) as exc:
        raise _schedule_error(exc) from exc
    _log(_admin_username(request), '批量删除数据行',
         f"{name}：删除 {result['deleted']} 行（提交 {result['requested']} 行）")
    return result


@app.post("/api/admin/data/tables/{name}/delete-page")
async def admin_data_delete_page(name: str, request: Request):
    """Delete every row on one preview page, using the same filters as the preview."""
    _require_permission(request, "data")
    body = await request.json()
    try:
        result = data_admin.delete_page(
            name, body.get("page") or 1, body.get("size") or 50,
            str(body.get("q") or ""), _admin_username(request))
    except (TypeError, ValueError, LookupError) as exc:
        raise _schedule_error(exc) from exc
    _log(_admin_username(request), '删除整页数据',
         f"{name} 第 {body.get('page') or 1} 页：删除 {result['deleted']} 行")
    return result


@app.delete("/api/admin/data/tables/{name}")
async def admin_data_delete(name: str, request: Request):
    _require_permission(request, "data")
    body = await request.json()
    try:
        result = data_admin.delete_row(name, body.get("keys") or {}, _admin_username(request))
    except (TypeError, ValueError, LookupError) as exc:
        raise _schedule_error(exc) from exc
    _log(_admin_username(request), '删除数据行',
         f"{name}：{'、'.join(f'{k}={v}' for k, v in (body.get('keys') or {}).items())}")
    return result


@app.get("/api/admin/anomalies")
async def admin_anomalies(request: Request, weeks: int = 2):
    _require_permission(request, "overview")
    return overview.anomaly_stats(weeks)


@app.get("/api/admin/overview")
async def admin_overview(request: Request, month: str = "", week: str = ""):
    _require_permission(request, "overview")
    try:
        return overview.overview(month or None, week=week.strip() or None)
    except ValueError as exc:
        raise _schedule_error(exc) from exc


@app.get("/api/member/schedule-list")
async def member_schedule_list(request: Request, week: str = ""):
    _require_member_permission(request, "schedule_list")
    try:
        return overview.member_schedule_week(week or None)
    except (ValueError, TypeError) as exc:
        raise _schedule_error(exc) from exc


@app.get("/api/schedules")
@app.get("/api/member/schedules")
async def schedules_get(request: Request, day: str):
    name = (_require_member(request) if request.url.path == "/api/member/schedules"
            else None if _admin_ok(request) else _require_member_permission(request, "duty"))
    if name is None:
        _require_permission(request, "overview")
    try:
        return {
            "items": schedule_store.list_day(day, name),
            "rules": duty_settings.load() if name is not None else None,
            "network_access": checkin_access.status(security.client_key(request)),
            "now": system_clock.public(),
        }
    except ValueError as exc:
        raise _schedule_error(exc) from exc


@app.get("/api/special-schedules")
async def special_schedules_get(request: Request, from_day: str = ""):
    name = None if _admin_ok(request) else _require_member_permission(request, "duty")
    if name is None:
        _require_permission(request, "special")
    try:
        return {"items": schedule_store.list_special(from_day or None, name)}
    except ValueError as exc:
        raise _schedule_error(exc) from exc


@app.get("/api/admin/clock")
async def admin_clock_get(request: Request):
    _require_permission(request, "clock")
    return system_clock.public()


@app.put("/api/admin/clock")
async def admin_clock_save(request: Request):
    _require_permission(request, "clock")
    body = await request.json()
    try:
        state = system_clock.save(
            automatic=body.get("automatic") is not False,
            hour=body.get("hour", 0), minute=body.get("minute", 0),
            day=str(body.get("date") or "") or None)
        _log(_admin_username(request), '修改系统时间',
             '自动设置' if state['automatic'] else f"{state['date']} {state['hour']:02d}:{state['minute']:02d}")
        return state
    except (TypeError, ValueError) as exc:
        raise _schedule_error(exc) from exc


@app.get("/api/admin/rules")
async def admin_rules_get(request: Request):
    _require_permission(request, "rules")
    return duty_settings.load()


@app.put("/api/admin/rules")
async def admin_rules_save(request: Request):
    _require_permission(request, "rules")
    try:
        rules = duty_settings.save(await request.json())
        _log(_admin_username(request), '修改值班时间设置',
             f"提前 {rules['early_minutes']} 分 / 迟到 {rules['late_minutes']} 分 / 旷班 {rules['absent_minutes']} 分")
        return rules
    except (ValueError, TypeError) as exc:
        raise _schedule_error(exc) from exc


@app.post("/api/schedules/{shift_id}/check-in")
async def schedule_check_in(shift_id: int, request: Request):
    name = _require_member_permission(request, "duty")
    _require_checkin_network(request, "checkin")
    try:
        stamp, chained = schedule_store.check_in_with_chain(shift_id, name)
        _log(name, '到岗签到',
             f"班次 #{shift_id}" + (f"，并自动签到 {len(chained)} 个连班" if chained else ''),
             role='member')
        return {"checked_at": stamp, "chained": chained}
    except (ValueError, LookupError) as exc:
        raise _schedule_error(exc) from exc


@app.get("/api/admin/attendance-history")
async def admin_attendance_history(request: Request):
    _require_permission(request, "overview")
    return {"items": schedule_store.history()}


@app.delete("/api/admin/schedules")
async def schedules_clear(request: Request):
    _require_permission(request, "weekly")
    result = schedule_store.clear_all()
    _log(_admin_username(request), '清除所有排班',
         f"删除 {result['deleted']} 个班次，归档 {result.get('archived', 0)} 条打卡")
    return result


@app.get("/api/admin/schedules/week")
async def schedules_week(request: Request, start: str = ""):
    """Totals for one Monday–Sunday week, shown before deleting it."""
    _require_permission(request, "overview")
    try:
        return schedule_store.week_summary(start)
    except (TypeError, ValueError) as exc:
        raise _schedule_error(exc) from exc


@app.delete("/api/admin/schedules/week")
async def schedules_week_clear(request: Request, start: str = ""):
    _require_permission(request, "overview")
    try:
        result = schedule_store.clear_week(start)
    except (TypeError, ValueError) as exc:
        raise _schedule_error(exc) from exc
    _log(_admin_username(request), '删除整周排班',
         f"{result['week_start']} 至 {result['week_end']}：删除 {result['deleted']} 个班次，"
         f"归档 {result['archived']} 条打卡")
    return result


@app.post("/api/admin/schedules")
async def schedule_create(request: Request):
    _require_permission(request, "overview")
    try:
        body = await request.json()
        shift_id = schedule_store.save(body)
        _log(_admin_username(request), '新增班次', _shift_summary(body))
        return {"id": shift_id}
    except (ValueError, TypeError, LookupError) as exc:
        raise _schedule_error(exc) from exc


@app.post("/api/admin/special-schedules")
async def special_schedule_create(request: Request):
    _require_permission(request, "special")
    body = await request.json()
    try:
        shift_id = schedule_store.save({**body, "special": True})
        _log(_admin_username(request), '新增特殊排班', _shift_summary(body))
        return {"id": shift_id}
    except (ValueError, TypeError, LookupError) as exc:
        raise _schedule_error(exc) from exc


@app.put("/api/admin/special-schedules/{shift_id}")
async def special_schedule_update(shift_id: int, request: Request):
    _require_permission(request, "special")
    body = await request.json()
    try:
        schedule_store.save({**body, "special": True}, shift_id, expected_special=True)
        _log(_admin_username(request), '修改特殊排班', f'#{shift_id} {_shift_summary(body)}')
        return {"id": shift_id}
    except (ValueError, TypeError, LookupError) as exc:
        raise _schedule_error(exc) from exc


@app.delete("/api/admin/special-schedules/{shift_id}")
async def special_schedule_delete(shift_id: int, request: Request):
    _require_permission(request, "special")
    try:
        schedule_store.delete(shift_id, expected_special=True)
        _log(_admin_username(request), '删除特殊排班', f'#{shift_id}')
        return {"ok": True}
    except LookupError as exc:
        raise _schedule_error(exc) from exc


@app.put("/api/admin/schedules/{shift_id}")
async def schedule_update(shift_id: int, request: Request):
    _require_permission(request, "overview")
    try:
        body = await request.json()
        schedule_store.save(body, shift_id)
        _log(_admin_username(request), '修改班次', f'#{shift_id} {_shift_summary(body)}')
        return {"id": shift_id}
    except (ValueError, TypeError, LookupError) as exc:
        raise _schedule_error(exc) from exc


@app.delete("/api/admin/schedules/{shift_id}")
async def schedule_delete(shift_id: int, request: Request):
    _require_permission(request, "overview")
    try:
        schedule_store.delete(shift_id)
        _log(_admin_username(request), '删除班次', f'#{shift_id}')
        return {"ok": True}
    except LookupError as exc:
        raise _schedule_error(exc) from exc


@app.get("/api/admin/weekly-roster")
async def weekly_get(request: Request):
    _require_permission(request, "weekly")
    return weekly_store.load()


@app.put("/api/admin/weekly-roster")
async def weekly_save(request: Request):
    _require_permission(request, "weekly")
    try:
        roster = weekly_store.save(await request.json())
        _log(_admin_username(request), '保存每周排班模板',
             f"{len(roster.get('slots') or [])} 个班次")
        # Only after the reviewed roster is stored do we create missing duty accounts.
        created = member_store.create_missing(
            {name for slot in roster['slots'] for names in slot['days'] for name in names})
        return {**roster, "members_created": created['created']}
    except (ValueError, TypeError) as exc:
        raise _schedule_error(exc) from exc


@app.post("/api/admin/weekly-roster/publish")
async def weekly_publish(request: Request):
    _require_permission(request, "weekly")
    try:
        body = await request.json()
        result = weekly_store.publish_until(
            str(body.get("end_date") or ""), str(body.get("start_date") or "") or None)
        _log(_admin_username(request), '生成排班',
             f"{body.get('start_date') or '今天'} 至 {body.get('end_date') or ''}："
             f"新增/覆盖 {result['created']} 个，跳过 {result['skipped']} 个")
        return result
    except (ValueError, TypeError) as exc:
        raise _schedule_error(exc) from exc


def _require_member(request: Request) -> str:
    name = str(request.session.get("member_username") or "")
    if (not name or not member_store.session_valid(name, request.session.get("member_version"))
            or not session_policy.state(request.session.get("member_sid"), "member", name)):
        raise HTTPException(status_code=401, detail="请先登录值班账号")
    return name


def _require_member_permission(request: Request, permission: str) -> str:
    name = _require_member(request)
    if not permissions.has(name, permission):
        raise HTTPException(status_code=403, detail="当前权限组不能访问此页面")
    return name


@app.get("/api/member/status")
async def member_status(request: Request):
    clock = system_clock.public()
    try:
        name = _require_member(request)
        return JSONResponse({"logged_in": True, "username": name, "permissions": sorted(permissions.permissions_of(name)), "now": clock,
                "rules": duty_settings.load(),
                "network_access": checkin_access.status(security.client_key(request))}, headers={"Cache-Control": "no-store"})
    except HTTPException:
        return {"logged_in": False, "username": "", "now": clock}


@app.post("/api/member/login")
async def member_login(request: Request):
    key = "member:" + security.client_key(request)
    if MEMBER_LIMITER.blocked(key):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请 5 分钟后重试")
    body = await request.json()
    name = str(body.get("username") or "").strip()
    # The 原始管理员 is an administrator identity only; it must never open a duty session.
    version = None if root_admin.is_root(name) else member_store.authenticate(
        name, str(body.get("password") or ""))
    if not version:
        MEMBER_LIMITER.fail(key)
        raise HTTPException(status_code=401, detail="姓名或密码不正确，或账号已停用")
    MEMBER_LIMITER.reset(key)
    _log(name, '登录值班账号', role='member')
    request.session["member_username"] = name
    request.session["member_version"] = version
    request.session["member_sid"] = session_policy.create("member", name)
    return {"ok": True, "username": name}


@app.get("/api/member/field-duty")
async def member_field_duty(request: Request):
    name = _require_member(request)
    return {**field_duty.member_status(name), "network_access": checkin_access.status(security.client_key(request))}


@app.get("/api/member/field-duty/pending")
async def member_field_pending(request: Request):
    _require_member_permission(request, "field_confirm")
    return field_duty.pending()


@app.post("/api/member/field-duty/pending/{username}")
async def member_field_confirm(username: str, request: Request):
    _require_member_permission(request, "field_confirm")
    try:
        result = field_duty.confirm(username.strip())
        _log(_require_member_permission(request, "field_confirm"), '确认外勤', username.strip(), role='member')
        return result
    except LookupError as exc:
        raise _schedule_error(exc) from exc


@app.delete("/api/member/field-duty/pending/{username}")
async def member_field_reject(username: str, request: Request):
    name = _require_member_permission(request, "field_confirm")
    try:
        result = field_duty.reject_pending(username.strip())
    except LookupError as exc:
        raise _schedule_error(exc) from exc
    _log(name, '删除外勤登记', username.strip(), role='member')
    return result


@app.post("/api/member/field-duty")
async def member_field_duty_register(request: Request):
    name = _require_member_permission(request, "member_field")
    _require_checkin_network(request, "field")
    result = field_duty.register(name)
    _log(name, '登记外勤', result.get('label', ''), role='member')
    return result


@app.put("/api/admin/overview/attendance")
async def admin_set_attendance(request: Request):
    _require_permission(request, "attendance_edit")
    body = await request.json()
    try:
        shift_id = int(body.get("shift_id"))
        name = str(body.get("name") or "").strip()
        result = overview.set_member_status(shift_id, name, str(body.get("status") or ""))
        attendance_live.publish()
        _log(_admin_username(request), '更改考勤状态',
             f"{name} 班次 #{shift_id} → {result['status']}")
        return result
    except (TypeError, ValueError) as exc:
        raise _schedule_error(exc) from exc
    except LookupError as exc:
        raise _schedule_error(exc) from exc


@app.put("/api/admin/field-duty")
async def admin_set_field_duty(request: Request):
    _require_permission(request, "field")
    body = await request.json()
    try:
        result = field_duty.set_participation(
            str(body.get("username") or "").strip(), bool(body.get("registered")),
            str(body.get("month") or "") or None)
        _log(_admin_username(request), '更改外勤状态',
             f"{result['username']} {result['month']} → {'已参加' if result['registered'] else '未参加'}")
        return result
    except (TypeError, ValueError) as exc:
        raise _schedule_error(exc) from exc


@app.get("/api/admin/swaps")
async def admin_swaps(request: Request):
    _require_permission(request, "swap")
    return {"items": swap_store.list_swaps()}


@app.get("/api/admin/swaps/candidates")
async def admin_swap_candidates(request: Request, username: str = "", shift_id: int = 0):
    _require_permission(request, "swap")
    name = username.strip()
    if not name:
        raise HTTPException(status_code=400, detail="请先选择人员")
    return {"items": swap_store.candidates(name, shift_id or None)}


@app.post("/api/admin/swaps")
async def admin_swap_create(request: Request):
    _require_permission(request, "swap")
    body = await request.json()
    try:
        swap_id = swap_store.create(
            int(body.get("shift_a")), str(body.get("name_a") or "").strip(),
            int(body.get("shift_b")), str(body.get("name_b") or "").strip(),
            confirmed=body.get("confirmed") is True)
        _log(_admin_username(request), '新增调班',
             f"{body.get('name_a')} 与 {body.get('name_b')} 互换班次")
    except (TypeError, ValueError, LookupError) as exc:
        raise _schedule_error(exc) from exc
    return {"id": swap_id}


@app.delete("/api/admin/swaps/{swap_id}")
async def admin_swap_delete(swap_id: int, request: Request):
    _require_permission(request, "swap")
    try:
        swap_store.delete(swap_id)
        _log(_admin_username(request), '删除调班', f'#{swap_id}')
    except (ValueError, LookupError) as exc:
        raise _schedule_error(exc) from exc
    return {"ok": True}


@app.get("/api/admin/field-duty")
async def admin_field_duty(request: Request, month: str = ""):
    _require_permission(request, "field")
    try:
        return field_duty.overview(month or None)
    except ValueError as exc:
        raise _schedule_error(exc) from exc


@app.get("/api/admin/field-duty/month")
async def admin_field_month(request: Request, month: str = ""):
    """Totals for one month, shown before deleting them."""
    _require_permission(request, "field")
    try:
        return field_duty.month_summary(month or None)
    except ValueError as exc:
        raise _schedule_error(exc) from exc


@app.delete("/api/admin/field-duty/month")
async def admin_field_month_clear(request: Request, month: str = ""):
    _require_permission(request, "field")
    try:
        result = field_duty.clear_month(month or None)
    except ValueError as exc:
        raise _schedule_error(exc) from exc
    _log(_admin_username(request), '删除整月外勤',
         f"{result['month']}：删除 {result['deleted']} 条记录（含已确认 {result['confirmed']} 条）")
    return result


@app.post("/api/admin/root-password")
async def admin_root_password(request: Request):
    """The 原始管理员 rotates its own password; nobody else can reach this."""
    actor = _session_account(request)
    if not actor:
        raise HTTPException(status_code=401, detail="请先登录管理员")
    if not root_admin.is_root(actor):
        raise HTTPException(status_code=403, detail="仅原始管理员可以使用此入口")
    body = await request.json()
    try:
        root_admin.change_password(str(body.get("password") or ""))
        _log(actor, '修改原始管理员密码', '凭据文件已更新')
    except (ValueError, LookupError) as exc:
        raise _schedule_error(exc) from exc
    return {"ok": True}


@app.post("/api/member/logout")
async def member_logout(request: Request):
    session_policy.revoke(request.session.pop("member_sid", None))
    request.session.pop("member_username", None)
    request.session.pop("member_version", None)
    return {"ok": True}


@app.post("/api/member/password")
async def member_password(request: Request):
    name = _require_member(request)
    body = await request.json()
    try:
        member_store.change_password(name, str(body.get("old_password") or ""), str(body.get("new_password") or ""))
        _log(name, '修改个人密码', role='member')
    except ValueError as exc:
        raise _schedule_error(exc) from exc
    request.session["member_version"] = member_store.session_version(name)
    return {"ok": True}


@app.get("/api/admin/push")
async def push_get(request: Request):
    _require_permission(request, "push")
    return push_reports.status()


@app.put("/api/admin/push")
async def push_save(request: Request):
    actor = _require_permission(request, "push")
    try:
        config = push_reports.save(await request.json())
    except (ValueError, TypeError) as exc:
        raise _schedule_error(exc) from exc
    _log(actor, '保存消息推送', f"每 {config['weeks']} 周，周日 {config['send_time']}，{len(config['recipients'])} 个目标邮箱")
    return push_reports.status()


@app.post("/api/admin/push/preview")
async def push_preview(request: Request):
    _require_permission(request, "push")
    body = await request.json()
    try:
        if not isinstance(body, dict):
            raise ValueError('预览配置格式不正确')
        config = push_reports.validate(body.get('config'))
        return push_reports.report(config, sample=body.get('sample') is True)
    except (ValueError, TypeError) as exc:
        raise _schedule_error(exc) from exc


@app.get("/api/admin/permission-groups")
async def permission_groups_list(request: Request):
    _require_permission(request, "permission_groups")
    return JSONResponse({"items": permissions.list_groups(), "tree": permissions.TREE},
                        headers={"Cache-Control": "no-store"})


@app.post("/api/admin/permission-groups")
async def permission_group_create(request: Request):
    _require_permission(request, "permission_groups")
    body = await request.json()
    try:
        group = permissions.save_group(body.get("name"), body.get("permissions", []))
    except (ValueError, LookupError) as exc:
        raise _schedule_error(exc) from exc
    _log(_admin_username(request), '新建权限组', group['name'])
    return group


@app.put("/api/admin/permission-groups/{group_id}")
async def permission_group_update(group_id: str, request: Request):
    actor = _require_permission(request, "permission_groups")
    body = await request.json()
    try:
        group = permissions.save_group(body.get("name"), body.get("permissions"), group_id)
    except (ValueError, LookupError) as exc:
        raise _schedule_error(exc) from exc
    _log(actor, '修改权限组', f"{group['name']}：{'、'.join(group['permissions']) or '无页面权限'}")
    return group


@app.delete("/api/admin/permission-groups/{group_id}")
async def permission_group_delete(group_id: str, request: Request):
    actor = _require_permission(request, "permission_groups")
    try:
        result = permissions.delete_group(group_id)
    except (ValueError, LookupError) as exc:
        raise _schedule_error(exc) from exc
    _log(actor, '删除权限组', f"{group_id}：{result['moved']} 个账号移入会员组")
    return result


@app.get("/api/admin/members")
async def admin_members(request: Request):
    actor = _require_permission(request, "members")
    return {
        "items": member_store.list_accounts(),
        "groups": permissions.list_groups(),
        "me": actor,
        "can_grant": True,
    }


@app.delete("/api/admin/members/sso")
async def admin_member_sso_unlink(request: Request, username: str = ""):
    actor = _require_permission(request, "members")
    name = username.strip()
    if not name:
        raise HTTPException(400, "请指定账号")
    _reject_root(name)
    try:
        result = keycloak.unlink_account(name)
    except (ValueError, LookupError) as exc:
        raise _schedule_error(exc) from exc
    _log(actor, '解除 SSO 绑定', name)
    return result


@app.put("/api/admin/members/permissions")
async def admin_member_permissions(request: Request):
    _require_permission(request, "members")
    body = await request.json()
    name = str(body.get("username") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="请指定账号")
    if "permissions" in body:
        raise HTTPException(status_code=400, detail="用户只能选择所属权限组，请在权限组管理中修改访问范围")
    _reject_root(name)
    try:
        perms = permissions.set_group(name, body.get("group_id"))
        _log(_admin_username(request), '更改所属权限组', f"{name} → {body.get('group_id')}")
    except (ValueError, LookupError) as exc:
        raise _schedule_error(exc) from exc
    return {"ok": True, "username": name, "group_id": body['group_id'], "permissions": sorted(perms)}


@app.put("/api/admin/members/password")
async def admin_member_password(request: Request):
    actor = _require_permission(request, "members")
    body = await request.json()
    name = str(body.get("username") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="请指定账号")
    if root_admin.is_root(name):
        # Only the root itself may rotate its own password, through its own endpoint.
        if name != actor:
            raise HTTPException(status_code=403, detail="原始管理员的密码只能由本人修改")
        raise HTTPException(status_code=400, detail="原始管理员请使用专用入口修改密码")
    # Only the account itself or a holder of "更改权限" may set a password.
    if name != actor and not permissions.has(actor, "members"):
        raise HTTPException(status_code=403, detail="仅账号本人或拥有“更改权限”者可以更改密码")
    try:
        member_store.set_password(name, str(body.get("password") or ""))
        _log(_admin_username(request), '更改账号密码', name)
    except (ValueError, LookupError) as exc:
        raise _schedule_error(exc) from exc
    return {"ok": True}


@app.post("/api/admin/members/provision")
async def admin_provision_members(request: Request):
    actor = _session_account(request)
    if not actor:
        raise HTTPException(status_code=401, detail="请先登录管理员")
    if not any(permissions.has(actor, p) for p in ('members', 'weekly')):
        raise HTTPException(status_code=403, detail="当前账号没有此权限")
    result = member_store.provision()
    _log(_admin_username(request), '补齐排班人员账号',
         f"新建 {result['created']} 个，已存在 {result['existing']} 个")
    return result


@app.post("/api/admin/members/cleanup")
async def admin_cleanup_members(request: Request):
    actor = _require_permission(request, "members")
    result = member_store.cleanup_unused()
    _log(actor, '清理多余用户', f"删除 {result['deleted']} 个：{'、'.join(result['usernames'])}")
    return result


@app.post("/api/admin/members")
async def admin_add_member(request: Request):
    _require_permission(request, "members")
    body = await request.json()
    try:
        result = member_store.create(body.get("username"))
        _log(_admin_username(request), '添加账号', str(body.get("username") or ""))
        return result
    except ValueError as exc:
        raise _schedule_error(exc) from exc


@app.delete("/api/admin/members")
async def admin_delete_member(request: Request, username: str = ""):
    actor = _require_permission(request, "members")
    name = username.strip()
    if not name:
        try:
            name = str((await request.json()).get("username") or "").strip()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="请指定要删除的账号") from exc
    _reject_root(name)
    if not _may_manage(name, actor):
        raise HTTPException(status_code=403, detail="该账号拥有权限，需具备更改权限的资格才能删除")
    try:
        member_store.delete(name)
        _log(_admin_username(request), '删除账号', name)
    except (ValueError, LookupError) as exc:
        raise _schedule_error(exc) from exc
    return {"ok": True}


@app.patch("/api/admin/members")
async def admin_update_member(request: Request):
    actor = _require_permission(request, "members")
    body = await request.json()
    if "active" in body and not isinstance(body["active"], bool):
        raise HTTPException(status_code=400, detail="账号状态格式错误")
    target = str(body.get("username") or "").strip()
    _reject_root(target)
    # Changing state or resetting a password of a privileged account needs the grant right.
    if not _may_manage(target, actor):
        raise HTTPException(status_code=403, detail="该账号拥有权限，需具备更改权限的资格才能操作")
    try:
        target = str(body.get("username") or "")
        member_store.update(target, body.get("active"), body.get("reset_password") is True)
        changes = []
        if "active" in body:
            changes.append('启用' if body.get("active") else '停用')
        if body.get("reset_password") is True:
            changes.append('重置密码')
        _log(_admin_username(request), '修改账号', f"{target}：{'、'.join(changes) or '无变化'}")
    except ValueError as exc:
        raise _schedule_error(exc) from exc
    return {"ok": True}


@app.get("/api/admin/session-timeout")
async def admin_session_timeout_get(request: Request):
    _require_permission(request, "rules")
    return session_policy.settings()


@app.put("/api/admin/session-timeout")
async def admin_session_timeout_save(request: Request):
    actor = _require_permission(request, "rules")
    try:
        value = session_policy.save(await request.json())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _log(actor, '修改无操作自动退出设置', f"无操作：成员 {value['member_minutes']} 分钟，管理 {value['admin_minutes']} 分钟；无响应：成员 {value['member_response_seconds']} 秒，管理 {value['admin_response_seconds']} 秒")
    return value


def _idle_identity(request: Request, role: str):
    if role == "admin":
        name = _session_account(request)
        handle = getattr(request.state, "admin_session_id", None) or request.session.get("admin_sid")
    elif role == "member":
        try:
            name = _require_member(request)
        except HTTPException:
            name = ""
        handle = request.session.get("member_sid")
    else:
        raise HTTPException(400, "登录端类型不正确")
    return name, handle


@app.get("/api/session/idle")
async def session_idle(request: Request, role: str = "member"):
    name, handle = _idle_identity(request, role)
    record = session_policy.state(handle, role, name) if name else None
    value = {"logged_in": bool(record), "server_now": time.time(),
             "idle_minutes": session_policy.settings()[role + "_minutes"],
             "expires_at": record['expires_at'] if record else None}
    return JSONResponse(value, headers={"Cache-Control": "no-store"})


@app.post("/api/session/activity")
async def session_activity(request: Request, role: str = "member"):
    name, handle = _idle_identity(request, role)
    if not name:
        raise HTTPException(401, "登录已超时，请重新登录")
    body = await request.json()
    try:
        active = session_policy.touch(handle, role, name, body.get("activity_at"))
    except (ValueError, AttributeError) as exc:
        raise HTTPException(400, "操作时间格式不正确") from exc
    if not active:
        raise HTTPException(401, "登录已超时，请重新登录")
    response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
    if role == "admin":
        _set_admin_cookie(response, name, handle, version=request.state.admin_session_version)
    return response


@app.get("/api/admin/sso")
async def admin_sso_get(request: Request):
    _require_permission(request, "sso")
    from server import sso_settings
    return sso_settings.public()


@app.put("/api/admin/sso")
async def admin_sso_save(request: Request):
    actor = _require_permission(request, "sso")
    from server import sso_settings
    try:
        result = sso_settings.save(await request.json())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _log(actor, '保存 SSO 配置', '启用' if result['enabled'] else '停用')
    return result


@app.post("/api/admin/sso/test")
async def admin_sso_test(request: Request):
    _require_permission(request, "sso")
    from server import sso_settings
    try:
        value = sso_settings.prepare(await request.json())
        sso_settings.validate(value, require_complete=True)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        metadata = await keycloak.discovery(value)
    except Exception:
        raise HTTPException(502, '无法读取身份提供方配置，请检查地址、证书及网络连接') from None
    return {"ok": True, "message": "身份提供方连接正常；客户端授权与用户名读取需实际登录验证。",
            "claims": [field for field in metadata.get('claims_supported', [])
                       if field in {'preferred_username', 'name', 'given_name', 'family_name', 'email'}]}


@app.get("/auth/keycloak/bind")
async def keycloak_binding_page():
    return FileResponse(WEB / "keycloak-bind.html", headers={"Cache-Control": "no-store"})


from server import keycloak
app.include_router(keycloak.router)


@app.get("/{slug}")
async def admin_page(slug: str, request: Request):
    if len(slug) != 8 or slug != admin_store.path():
        raise HTTPException(status_code=404, detail="页面不存在")
    request.session["admin_gate"] = slug
    return FileResponse(
        WEB / "admin.html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/api/network-access")
async def network_access_status(request: Request):
    return JSONResponse(checkin_access.status(security.client_key(request)), headers={"Cache-Control": "no-store"})


def _require_checkin_network(request: Request, kind: str):
    if not checkin_access.status(security.client_key(request))[kind]:
        raise HTTPException(403, f"当前网络不允许{checkin_access.KINDS[kind]}，请连接允许的网络后重试")


@app.get("/api/admin/checkin-access")
async def admin_checkin_access_get(request: Request):
    _require_permission(request, "checkin_manage")
    return {"rules": checkin_access.load(), "network_access": checkin_access.status(security.client_key(request))}


@app.put("/api/admin/checkin-access")
async def admin_checkin_access_save(request: Request):
    actor = _require_permission(request, "checkin_manage")
    try:
        rules = checkin_access.save(await request.json())
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    _log(actor, '修改打卡网络限制', '更新打卡和外勤的 IP 允许/排除规则')
    return {"rules": rules, "network_access": checkin_access.status(security.client_key(request))}


@app.post("/api/session/heartbeat")
async def session_heartbeat(request: Request, role: str = "member"):
    name, handle = _idle_identity(request, role)
    if not name or not session_policy.heartbeat(handle, role, name):
        raise HTTPException(401, "设备响应已超时，请重新登录")
    return JSONResponse({"ok": True, "timeout_seconds": session_policy.response_timeout(role)},
                        headers={"Cache-Control": "no-store"})


@app.get("/api/attendance/live")
async def attendance_updates(request: Request, role: str = "member", after: str = ""):
    if len(after) > 64:
        raise HTTPException(400, "更新版本格式错误")
    def authorize():
        if role == "admin":
            _require_permission(request, "overview")
        elif role == "member":
            _require_member(request)
        else:
            raise HTTPException(400, "登录端类型不正确")
    authorize()
    value = await attendance_live.wait(after)
    authorize()  # Revocation during a long poll must take effect before replying.
    return JSONResponse({"revision": value}, headers={"Cache-Control": "no-store"})
