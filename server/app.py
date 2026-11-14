from __future__ import annotations

import asyncio
from contextlib import suppress
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer
from starlette.middleware.sessions import SessionMiddleware

from server import attendance_live, checkin_access, session_policy, admin_store, data_admin, db, duty_settings, field_duty, log_rotation, mailer, operation_log, overview, permissions, push_reports, table_rotation, root_admin, schedule_store, swap_store, system_clock, weekly_store, member_store, security

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
DATA = ROOT / "data"
ICON = ROOT / "icon"


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


async def _attendance_status_worker():
    while True:
        try:
            schedule_store.finalize_absences()
        except Exception as exc:
            print(f'[attendance] 状态确认暂时失败：{type(exc).__name__}')
        await asyncio.sleep(30)


async def _log_rotation_worker():
    """Keep data/logs, the operation log and the capped record tables within their size cap.

    Checking file sizes is cheap, but there is no reason to do it on every request, so
    it runs every five minutes alongside the other background workers.
    """
    while True:
        try:
            log_rotation.prune()
            operation_log.prune()
            table_rotation.prune()
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
    public_base = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/") or "http://127.0.0.1:50025"
    print(f"管理员界面: {public_base}/{slug}")
    if not admin_store.has_user():
        print("尚未注册管理员，首次打开站点会跳转到该地址并创建账号。")
    print("")
    push_task = asyncio.create_task(push_reports.worker())
    presence_task = asyncio.create_task(session_policy.presence_worker())
    log_task = asyncio.create_task(_log_rotation_worker())
    attendance_task = asyncio.create_task(_attendance_status_worker())
    try:
        yield
    finally:
        for task in (push_task, presence_task, log_task, attendance_task):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


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
# Canonical account limits isolate failures across users sharing a proxy or NAT.
MEMBER_LIMITER = security.AttemptLimiter(max_attempts=security.MEMBER_MAX_ATTEMPTS,
                                         window=security.MEMBER_WINDOW_SECONDS)
# A shared source can throttle failed requests, but must not deny valid credentials.
MEMBER_IP_LIMITER = security.AttemptLimiter(max_attempts=security.MEMBER_IP_MAX_ATTEMPTS,
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


@app.get("/healthz")
async def healthz():
    """Liveness: the process can answer HTTP. Deliberately does not touch the DB."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz():
    """Readiness: the application can reach its database."""
    try:
        with db.read() as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception:
        return JSONResponse({"status": "unready"}, status_code=503)
    return {"status": "ready"}


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
    if not permissions.has(name, permission):
        raise HTTPException(status_code=403, detail="当前账号没有此权限")
    return name


def _reject_root(name: str) -> None:
    """The 原始管理员 is managed only through its own file, never these endpoints."""
    if root_admin.is_root(name):
        raise HTTPException(status_code=403, detail="原始管理员不可在此修改，只能修改其密码")


def _require_default_account_grant(actor: str) -> None:
    # A customized default group must not provide a back door around group assignment.
    with db.read() as conn:
        default = conn.execute('SELECT permissions FROM permission_groups WHERE id=?',
                               (permissions.DEFAULT_GROUP,)).fetchone()
    granted = permissions.decode(default['permissions']) if default else set(permissions.DEFAULT_PERMISSIONS)
    if granted & permissions.ADMIN_PERMISSIONS and not permissions.has(actor, 'permission_groups'):
        raise HTTPException(403, "默认权限组包含管理权限，创建账号需要权限组管理权限")


def _may_manage(target: str, actor: str) -> bool:
    """Without the grant permission, an account cannot touch a privileged account.

    A plain duty account (only the user permission) is fair game; anyone holding any
    administrative permission is protected from accounts that cannot grant permissions.
    """
    if target == actor:
        return True
    if permissions.has(actor, "permission_groups"):
        return True
    return not permissions.is_admin(target, include_inactive=True)


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
        "can_grant": bool(name and permissions.has(name, "permission_groups")),
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
    key = "admin:" + security.login_client_key(request)
    if ADMIN_LIMITER.blocked(key):
        raise HTTPException(status_code=429, detail="管理员登录尝试过于频繁，请在限制开始 10 分钟后重试")
    body = await request.json()
    login_name = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    username = member_store.resolve_login_name(login_name)
    account_key = "admin:" + username.casefold()
    if ACCOUNT_LIMITER.blocked(account_key):
        raise HTTPException(status_code=429, detail="管理员登录尝试过于频繁，请在限制开始 10 分钟后重试")
    # The 原始管理员 authenticates against its own file, never the account table.
    is_root = root_admin.is_root(login_name)
    if is_root:
        version = root_admin.session_version() if root_admin.verify(password) else None
    else:
        account = member_store.authenticate_login(login_name, password)
        username, version = account if account else (username, None)
    if not version:
        ADMIN_LIMITER.fail(key)
        ACCOUNT_LIMITER.fail(account_key)
        raise HTTPException(status_code=401, detail="用户名或密码不正确")
    # Administrator pages are open to any account with at least one admin permission.
    if not is_root and not permissions.is_admin(username):
        ADMIN_LIMITER.fail(key)
        ACCOUNT_LIMITER.fail(account_key)
        raise HTTPException(status_code=403, detail="该账号没有管理员权限")
    ADMIN_LIMITER.reset(key)
    ACCOUNT_LIMITER.reset(account_key)
    _log(username, '登录管理端')
    request.session["admin_ok"] = True
    request.session["admin_username"] = username
    request.session["admin_version"] = version
    request.session["admin_sid"] = session_policy.create("admin", username)
    response = JSONResponse({"ok": True, "username": username})
    _set_admin_cookie(response, username, request.session["admin_sid"], version=version)
    return response


@app.post("/api/admin/logout")
async def admin_logout(request: Request) -> JSONResponse:
    actor = _session_account(request)
    if actor:
        _log(actor, '退出管理端')
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


def _schedule_error(exc):
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc) or "排班数据格式不正确")


@app.get("/api/admin/logs")
async def admin_logs(request: Request, limit: int = 100, actor: str = "", q: str = "", offset: int = 0):
    _require_permission(request, "logs")
    limit = max(1, min(int(limit or 100), 100))
    offset = max(0, offset)
    items = operation_log.list_entries(limit, actor.strip(), q.strip(), offset)
    total = operation_log.count_entries(actor.strip(), q.strip())
    has_more = offset + limit < total
    return {"items": items, "has_more": has_more, "total": total,
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
    actor = _require_permission(request, "weekly")
    try:
        roster = weekly_store.save(await request.json())
        _log(_admin_username(request), '保存每周排班模板',
             f"{len(roster.get('slots') or [])} 个班次")
        # Only after the reviewed roster is stored do we create missing duty accounts.
        created = member_store.create_missing(
            {name for slot in roster['slots'] for names in slot['days'] for name in names},
            authorize=lambda: _require_default_account_grant(actor))
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
    source = 'member:source:' + security.login_client_key(request)
    body = await request.json()
    login_name = str(body.get("username") or "").strip()
    canonical = member_store.resolve_login_name(login_name)
    key = security.member_login_account_key(canonical)
    if MEMBER_LIMITER.blocked(key):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请 5 分钟后重试")
    # Verify before the source backstop: shared proxy failures cannot block another
    # account's correct credentials. Per-account limits still guard credential checks.
    source_blocked = MEMBER_IP_LIMITER.blocked(source)
    account = member_store.authenticate_login(login_name, str(body.get("password") or ""))
    if not account:
        MEMBER_LIMITER.fail(key)
        MEMBER_IP_LIMITER.fail(source)
        if source_blocked:
            raise HTTPException(status_code=429, detail="登录尝试过于频繁，请 5 分钟后重试")
        raise HTTPException(status_code=401, detail="姓名或密码不正确，或账号已停用")
    name, version = account
    actual_key = security.member_login_account_key(name)
    if actual_key != key and MEMBER_LIMITER.blocked(actual_key):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请 5 分钟后重试")
    MEMBER_LIMITER.reset(actual_key)
    # A successful account must not erase an attacker's accumulated source failures.
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
    actor = _require_member_permission(request, "field_confirm")
    return field_duty.pending(exclude_username=actor)


@app.post("/api/member/field-duty/pending/{username}")
async def member_field_confirm(username: str, request: Request):
    actor = _require_member_permission(request, "field_confirm")
    target = username.strip()
    if target == actor:
        raise HTTPException(status_code=403, detail="不能确认本人的外勤登记，请由其他成员确认")
    try:
        result = field_duty.confirm(target, actor=actor)
        _log(actor, '确认外勤', target, role='member')
        return result
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
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
        "can_grant": permissions.has(actor, "permission_groups"),
    }


@app.delete("/api/admin/members/sso")
async def admin_member_sso_unlink(request: Request, username: str = ""):
    actor = _require_permission(request, "members")
    name = username.strip()
    if not name:
        raise HTTPException(400, "请指定账号")
    _reject_root(name)
    if not _may_manage(name, actor):
        raise HTTPException(403, "操作管理员账号需要权限组管理权限")
    try:
        result = keycloak.unlink_account(name)
    except (ValueError, LookupError) as exc:
        raise _schedule_error(exc) from exc
    _log(actor, '解除 SSO 绑定', name)
    return result


@app.put("/api/admin/members/permissions")
async def admin_member_permissions(request: Request):
    _require_permission(request, "members")
    _require_permission(request, "permission_groups")
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


@app.get("/api/admin/members/student-ids/export")
async def export_member_student_ids(request: Request):
    from server import member_csv
    _require_permission(request, "members")
    return Response(member_csv.export_csv(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="student-ids.csv"',
                             "Cache-Control": "no-store"})


@app.post("/api/admin/members/student-ids/import")
async def import_member_student_ids(request: Request):
    from server import member_csv
    actor = _require_permission(request, "members")
    try:
        rows = member_csv.parse_csv(await request.body())
        result = member_csv.apply_rows(
            [{'username': name, 'student_id': number} for name, number in rows],
            lambda name: _may_manage(name, actor))
    except ValueError as exc:
        raise _schedule_error(exc) from exc
    _log(actor, '导入学号名单', f"导入 {result['updated']} 个，冲突 {len(result['conflicts'])} 个")
    return result


@app.post("/api/admin/members/student-ids/replace")
async def replace_member_student_ids(request: Request):
    from server import member_csv
    actor = _require_permission(request, "members")
    body = await request.json()
    rows = body.get('items') if isinstance(body, dict) else None
    if (not isinstance(rows, list) or not rows or len(rows) > 10000 or
            any(not isinstance(row, dict) or any(not isinstance(row.get(key), str)
                for key in ('username', 'student_id', 'original_id')) for row in rows)):
        raise HTTPException(400, '替代数据格式不正确')
    result = member_csv.apply_rows(rows, lambda name: _may_manage(name, actor), replace=True)
    _log(actor, '替代冲突学号', f"替代 {result['updated']} 个，仍有冲突 {len(result['conflicts'])} 个")
    return result


@app.put("/api/admin/members/student-id")
async def admin_member_student_id(request: Request):
    actor = _require_permission(request, "members")
    body = await request.json()
    name = str(body.get("username") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="请指定账号")
    _reject_root(name)
    if not _may_manage(name, actor):
        raise HTTPException(status_code=403, detail="操作管理员账号需要权限组管理权限")
    try:
        member_store.set_student_id(name, body.get("student_id"))
        _log(actor, '更改学号', name)
    except (ValueError, LookupError) as exc:
        raise _schedule_error(exc) from exc
    return {"ok": True}


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
    if not _may_manage(name, actor):
        raise HTTPException(status_code=403, detail="操作管理员账号需要权限组管理权限")
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
    _require_default_account_grant(actor)
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
    actor = _require_permission(request, "members")
    _require_default_account_grant(actor)
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


@app.get("/api/admin/about")
async def admin_about(request: Request):
    name = _session_account(request)
    if not name:
        raise HTTPException(status_code=401, detail="请先登录管理员")
    from server import wiki
    from server.app_version import VERSION
    guide = wiki.for_account(permissions.permissions_of(name), root_admin.is_root(name))
    return {"version": VERSION, "guide": guide}


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
