from __future__ import annotations

import hashlib
import secrets
import string
import threading
from typing import Any

from server import db

ALPHABET = string.ascii_lowercase + string.digits
ITERATIONS = 180_000
# Echoed in place of a stored credential; save handlers treat it as "keep the stored value".
MASKED = "••••••"

# Who may act as administrator is checked on every authenticated request. Caching the
# result avoids re-reading the whole settings table per request; every write below
# invalidates the cache, so correctness is unchanged.
_auth_cache: tuple[set[str], str] | None = None
_auth_lock = threading.Lock()


def _invalidate():
    global _auth_cache
    with _auth_lock:
        _auth_cache = None


def usernames() -> list[str]:
    return sorted(_admin_identity()[0])


def path() -> str:
    return _admin_identity()[1]


def _admin_identity() -> tuple[set[str], str]:
    global _auth_cache
    cached = _auth_cache
    if cached is not None:
        return cached
    with _auth_lock:
        if _auth_cache is not None:
            return _auth_cache
        with db.read() as conn:
            names = {row["username"] for row in conn.execute("SELECT username FROM users")}
            stored = db.get_setting(conn, "path", "")
        if not isinstance(stored, str) or len(stored) != 8:
            stored = _new_path()
            with db.write() as conn:
                db.set_setting(conn, "path", stored)
        _auth_cache = (names, stored)
        return _auth_cache


def _new_path() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(8))


def _hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), ITERATIONS)
    return f"{salt}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    if not stored or "$" not in stored:
        return False
    salt, _ = stored.split("$", 1)
    return secrets.compare_digest(_hash_password(password, salt), stored)


def _empty() -> dict[str, Any]:
    return {
        "path": _new_path(),
        "username": "",
        "password": "",
        "users": [],
        "model": {
            "provider": "openai",
            "openai_base": "https://api.openai.com/v1",
            "openai_key": "",
            "openai_model": "",
            "system_prompt": "",
            "keys_by_provider": {},
        },
        "mcp": {
            "mcp_url": "",
            "mcp_api_key": "",
            "mcp_heat": 50,
            "outline_access_token": "",
            "outline_refresh_token": "",
            "oauth_meta": {},
        },
        "qq": {
            "app_id": "",
            "app_secret": "",
            "enabled": False,
        },
        "smtp": {
            "host": "",
            "port": 465,
            "security": "ssl",
            "username": "",
            "password": "",
            "from_address": "",
            "from_name": "",
            "enabled": False,
        },
    }


def load() -> dict[str, Any]:
    with db.read() as conn:
        stored_path = db.get_setting(conn, "path", "")
        data = {
            "path": stored_path,
            "username": str(db.get_setting(conn, "username", "") or ""),
            "password": str(db.get_setting(conn, "password", "") or ""),
            "users": [
                {"username": str(row["username"] or ""), "password": str(row["password"] or "")}
                for row in conn.execute("SELECT username, password FROM users ORDER BY username")
            ],
            "model": db.get_setting(conn, "model", {}) or {},
            "mcp": db.get_setting(conn, "mcp", {}) or {},
            "qq": db.get_setting(conn, "qq", {}) or {},
            "smtp": db.get_setting(conn, "smtp", {}) or {},
        }
    dirty = False
    if not isinstance(data.get("path"), str) or len(data["path"]) != 8:
        data["path"] = _new_path()
        dirty = True
    if not isinstance(data.get("model"), dict):
        data["model"] = {}
        dirty = True
    if not isinstance(data.get("mcp"), dict):
        data["mcp"] = {}
        dirty = True
    if not isinstance(data.get("qq"), dict):
        data["qq"] = {}
        dirty = True
    if not isinstance(data.get("smtp"), dict):
        data["smtp"] = {}
        dirty = True
    for section in ("model", "mcp", "qq", "smtp"):
        for key, value in _empty()[section].items():
            if key not in data[section]:
                data[section][key] = value
                dirty = True
    if dirty or not stored_path:
        save(data)
    return data


def save(data: dict[str, Any]) -> None:
    with db.write() as conn:
        db.set_setting(conn, "path", str(data.get("path") or ""))
        db.set_setting(conn, "username", str(data.get("username") or ""))
        db.set_setting(conn, "password", str(data.get("password") or ""))
        db.set_setting(conn, "model", data.get("model") if isinstance(data.get("model"), dict) else {})
        db.set_setting(conn, "mcp", data.get("mcp") if isinstance(data.get("mcp"), dict) else {})
        db.set_setting(conn, "qq", data.get("qq") if isinstance(data.get("qq"), dict) else {})
        db.set_setting(conn, "smtp", data.get("smtp") if isinstance(data.get("smtp"), dict) else {})
    _invalidate()


def users() -> list[dict[str, Any]]:
    return list(load()["users"])


def has_user() -> bool:
    """True once an administrator has been created through the console page."""
    with db.read() as conn:
        if db.get_setting(conn, "setup_done", ""):
            return True
    return any(item.get("username") and item.get("password") for item in users())


def _validate_account(username: str, password: str) -> str:
    name = username.strip()
    if not name or len(name) > 64:
        raise ValueError("请填写用户名")
    if len(password) < 6:
        raise ValueError("密码至少 6 位")
    return name


def remember_setup(username: str) -> None:
    """Mark the console-facing setup as done; login now lives in member accounts."""
    with db.write() as conn:
        db.set_setting(conn, "setup_done", str(username))
    _invalidate()


def register(username: str, password: str) -> None:
    name = _validate_account(username, password)
    hashed = _hash_password(password)
    with db.write() as conn:
        if conn.execute("SELECT username FROM users LIMIT 1").fetchone():
            raise ValueError("管理员已注册，请直接登录")
        conn.execute("INSERT INTO users(username,password) VALUES(?,?)", (name, hashed))
    _invalidate()


def add_user(username: str, password: str) -> None:
    name = _validate_account(username, password)
    hashed = _hash_password(password)
    with db.write() as conn:
        if conn.execute("SELECT username FROM users WHERE username=?", (name,)).fetchone():
            raise ValueError("用户名已存在")
        conn.execute("INSERT INTO users(username,password) VALUES(?,?)", (name, hashed))
    _invalidate()


def update_user(old_username: str, username: str, password: str) -> str:
    old, name = old_username.strip(), username.strip()
    if not name or len(name) > 64:
        raise ValueError("请填写用户名")
    if password and len(password) < 6:
        raise ValueError("密码至少 6 位")
    with db.write() as conn:
        target = conn.execute("SELECT password FROM users WHERE username=?", (old,)).fetchone()
        if not target:
            raise ValueError("账号不存在")
        if name != old and conn.execute("SELECT username FROM users WHERE username=?", (name,)).fetchone():
            raise ValueError("用户名已存在")
        conn.execute("UPDATE users SET username=?,password=? WHERE username=?",
                     (name, _hash_password(password) if password else target["password"], old))
        # Remove obsolete single-admin fields to avoid resurrecting renamed accounts.
        db.set_setting(conn, "username", "")
        db.set_setting(conn, "password", "")
    _invalidate()
    return name


def delete_user(username: str, current_username: str = "") -> None:
    name = username.strip()
    if name == current_username.strip():
        raise ValueError("不能删除当前登录的管理员")
    with db.write() as conn:
        if not conn.execute("SELECT username FROM users WHERE username=?", (name,)).fetchone():
            raise ValueError("账号不存在")
        if len(conn.execute("SELECT username FROM users").fetchall()) <= 1:
            raise ValueError("不能删除最后一个管理员")
        conn.execute("DELETE FROM users WHERE username=?", (name,))
        db.set_setting(conn, "username", "")
        db.set_setting(conn, "password", "")
    _invalidate()


def verify_login(username: str, password: str) -> bool:
    with db.read() as conn:
        row = conn.execute("SELECT password FROM users WHERE username=?", (username.strip(),)).fetchone()
    return bool(row) and _verify_password(password, row["password"])


def model_config() -> dict[str, Any]:
    return dict(load().get("model") or {})


def save_model(body: dict[str, Any]) -> dict[str, Any]:
    data = load()
    model = data.setdefault("model", _empty()["model"])
    if "provider" in body:
        model["provider"] = str(body.get("provider") or "").strip() or "custom"
    if "openai_base" in body:
        model["openai_base"] = str(body.get("openai_base") or "").strip()
    if "openai_key" in body:
        key = str(body.get("openai_key") or "")
        if key and not set(key) <= {"•"}:
            model["openai_key"] = key
    if "openai_model" in body:
        model["openai_model"] = str(body.get("openai_model") or "").strip()
    if "system_prompt" in body:
        model["system_prompt"] = str(body.get("system_prompt") or "")
    if isinstance(body.get("keys_by_provider"), dict):
        # A masked value means "keep what is stored"; the key is never echoed back.
        current = model.get("keys_by_provider") if isinstance(model.get("keys_by_provider"), dict) else {}
        updated = {}
        for name, value in body["keys_by_provider"].items():
            if not isinstance(value, str):
                continue
            if value and set(value) <= {"•"}:
                if str(current.get(str(name)) or ""):
                    updated[str(name)] = str(current[str(name)])
            else:
                updated[str(name)] = value
        model["keys_by_provider"] = updated
    save(data)
    return model_public()


def system_prompt() -> str:
    from server.chat import SYSTEM_PROMPT

    text = str(model_config().get("system_prompt") or "").strip()
    return text or SYSTEM_PROMPT


def model_public() -> dict[str, Any]:
    """Never returns the stored API keys, only whether one is set.

    The whole 其他设置 page is reachable with any single settings card, so echoing a
    plaintext key here would hand the LLM credential to whoever holds only, say, SMTP.
    """
    model = model_config()
    key = model.get("openai_key") or ""
    providers = model.get("keys_by_provider") or {}
    return {
        "provider": model.get("provider") or "openai",
        "openai_base": model.get("openai_base") or "",
        "openai_model": model.get("openai_model") or "",
        "openai_key": MASKED if key.strip() else "",
        "system_prompt": system_prompt(),
        "has_key": bool(key.strip()),
        "keys_by_provider": {name: MASKED for name, value in providers.items() if str(value or "").strip()},
    }


def clamp_mcp_heat(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = 50
    return max(0, min(100, n))


def mcp_heat() -> int:
    mcp = mcp_config()
    if "mcp_heat" in mcp:
        return clamp_mcp_heat(mcp.get("mcp_heat"))
    return clamp_mcp_heat(model_config().get("mcp_heat", 50))


def chat_credentials() -> tuple[str, str, str, str]:
    model = model_config()
    return (
        (model.get("openai_base") or "").strip(),
        (model.get("openai_key") or "").strip(),
        (model.get("openai_model") or "").strip(),
        (model.get("provider") or "").strip(),
    )


def mcp_config() -> dict[str, Any]:
    return dict(load().get("mcp") or {})


def mcp_url() -> str:
    return (mcp_config().get("mcp_url") or "").strip()


def mcp_api_key() -> str:
    return (mcp_config().get("mcp_api_key") or "").strip()


def oauth_tokens() -> dict[str, Any]:
    mcp = mcp_config()
    meta = mcp.get("oauth_meta") if isinstance(mcp.get("oauth_meta"), dict) else {}
    return {
        "access_token": str(mcp.get("outline_access_token") or "").strip(),
        "refresh_token": str(mcp.get("outline_refresh_token") or "").strip(),
        "oauth_meta": dict(meta),
    }


def oauth_connected() -> bool:
    return bool(oauth_tokens()["access_token"])


def mcp_oauth_token() -> str:
    return oauth_tokens()["access_token"]


def save_oauth(access: str, refresh: str | None = None, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    data = load()
    mcp = data.setdefault("mcp", _empty()["mcp"])
    mcp["outline_access_token"] = str(access or "").strip()
    if refresh is not None:
        refresh_s = str(refresh or "").strip()
        if refresh_s:
            mcp["outline_refresh_token"] = refresh_s
        elif refresh == "":
            mcp["outline_refresh_token"] = ""
    if meta:
        mcp["oauth_meta"] = {
            "token_endpoint": str(meta.get("token_endpoint") or ""),
            "client_id": str(meta.get("client_id") or ""),
            "client_secret": str(meta.get("client_secret") or ""),
            "mcp_url": str(meta.get("mcp_url") or mcp.get("mcp_url") or ""),
        }
    save(data)
    return mcp_public()


def clear_oauth() -> dict[str, Any]:
    data = load()
    mcp = data.setdefault("mcp", _empty()["mcp"])
    mcp["outline_access_token"] = ""
    mcp["outline_refresh_token"] = ""
    mcp["oauth_meta"] = {}
    save(data)
    return mcp_public()


async def refresh_mcp_oauth() -> str:
    from server.oauth import refresh_access_token

    stored = oauth_tokens()
    refresh = stored["refresh_token"]
    meta = stored["oauth_meta"]
    if not refresh or not str(meta.get("token_endpoint") or "").strip():
        raise RuntimeError("知识库登录已过期，请在管理员界面重新点击「网协认证登陆」")
    tokens = await refresh_access_token(
        token_endpoint=str(meta.get("token_endpoint") or ""),
        client_id=str(meta.get("client_id") or ""),
        client_secret=str(meta.get("client_secret") or ""),
        refresh_token=refresh,
        mcp_url=str(meta.get("mcp_url") or mcp_url()),
    )
    access = str(
        tokens.get("access_token")
        or (tokens.get("data") or {}).get("access_token")
        or ""
    ).strip()
    if not access:
        raise RuntimeError("刷新知识库登录失败，请在管理员界面重新登录")
    save_oauth(access, tokens.get("refresh_token") or refresh, meta)
    return access


def mcp_public() -> dict[str, Any]:
    mcp = mcp_config()
    return {
        "mcp_url": mcp.get("mcp_url") or "",
        "mcp_heat": mcp_heat(),
        "oauth_connected": oauth_connected(),
    }


def save_mcp(body: dict[str, Any]) -> dict[str, Any]:
    from server.mcp_client import normalize_mcp_url

    data = load()
    mcp = data.setdefault("mcp", _empty()["mcp"])
    if "mcp_url" in body:
        raw = str(body.get("mcp_url") or "").strip()
        mcp["mcp_url"] = normalize_mcp_url(raw) if raw else ""
    if "mcp_heat" in body:
        mcp["mcp_heat"] = clamp_mcp_heat(body.get("mcp_heat"))
        model = data.get("model")
        if isinstance(model, dict):
            model.pop("mcp_heat", None)
    save(data)
    return mcp_public()


def qq_config() -> dict[str, Any]:
    cfg = dict(load().get("qq") or {})
    defaults = _empty()["qq"]
    for key, value in defaults.items():
        cfg.setdefault(key, value)
    return cfg


def qq_public() -> dict[str, Any]:
    qq = qq_config()
    secret = str(qq.get("app_secret") or "")
    return {
        "app_id": str(qq.get("app_id") or "").strip(),
        # Masked for the same reason as the model key: the page is shared by five cards.
        "app_secret": MASKED if secret.strip() else "",
        "has_secret": bool(secret.strip()),
        "enabled": bool(qq.get("enabled")),
        "oauth_connected": oauth_connected(),
    }


def save_qq(body: dict[str, Any]) -> dict[str, Any]:
    data = load()
    qq = data.setdefault("qq", _empty()["qq"])
    if "app_id" in body:
        qq["app_id"] = str(body.get("app_id") or "").strip()
    if "app_secret" in body:
        secret = str(body.get("app_secret") or "")
        if secret and not set(secret) <= {"•"}:
            qq["app_secret"] = secret
        elif secret == "":
            qq["app_secret"] = ""
    if "enabled" in body:
        qq["enabled"] = bool(body.get("enabled"))
    save(data)
    return qq_public()


SMTP_SECURITY = ("ssl", "starttls", "none")


def smtp_config() -> dict[str, Any]:
    cfg = dict(load().get("smtp") or {})
    for key, value in _empty()["smtp"].items():
        cfg.setdefault(key, value)
    return cfg


def smtp_public() -> dict[str, Any]:
    """Never returns the stored password, only whether one is set."""
    smtp = smtp_config()
    password = str(smtp.get("password") or "")
    return {
        "host": str(smtp.get("host") or "").strip(),
        "port": int(smtp.get("port") or 0),
        "security": str(smtp.get("security") or "ssl"),
        "username": str(smtp.get("username") or "").strip(),
        "password": MASKED if password else "",
        "has_password": bool(password),
        "from_address": str(smtp.get("from_address") or "").strip(),
        "from_name": str(smtp.get("from_name") or "").strip(),
        "enabled": bool(smtp.get("enabled")),
    }


def _smtp_port(value, security: str) -> int:
    if value in (None, ""):
        return 465 if security == "ssl" else 587 if security == "starttls" else 25
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("端口必须是数字") from exc
    if not 1 <= port <= 65535:
        raise ValueError("端口应在 1～65535 之间")
    return port


def _looks_like_address(value: str) -> bool:
    """Deliberately small: one @, something either side, a dot in the domain."""
    if value.count("@") != 1:
        return False
    local, domain = value.split("@")
    return bool(local and "." in domain and not domain.startswith(".") and not domain.endswith("."))


def save_smtp(body: dict[str, Any]) -> dict[str, Any]:
    data = load()
    smtp = data.setdefault("smtp", _empty()["smtp"])
    for key, value in _empty()["smtp"].items():
        smtp.setdefault(key, value)
    if "host" in body:
        smtp["host"] = str(body.get("host") or "").strip()
    if "security" in body:
        security = str(body.get("security") or "ssl").strip().lower()
        if security not in SMTP_SECURITY:
            raise ValueError("加密方式只能是 SSL、STARTTLS 或 None")
        smtp["security"] = security
    if "port" in body or "security" in body:
        smtp["port"] = _smtp_port(body.get("port"), str(smtp.get("security") or "ssl"))
    if "username" in body:
        smtp["username"] = str(body.get("username") or "").strip()
    if "password" in body:
        password = str(body.get("password") or "")
        # A masked echo means "keep the stored password".
        if password and not set(password) <= {"•"}:
            smtp["password"] = password
        elif password == "":
            smtp["password"] = ""
    if "from_address" in body:
        address = str(body.get("from_address") or "").strip()
        if address and not _looks_like_address(address):
            raise ValueError("发件邮箱格式不正确")
        smtp["from_address"] = address
    if "from_name" in body:
        smtp["from_name"] = str(body.get("from_name") or "").strip()
    if "enabled" in body:
        enabled = bool(body.get("enabled"))
        if enabled:
            # Refuse to enable an unusable configuration.
            if not smtp["host"]:
                raise ValueError("启用前请填写 SMTP 服务器地址")
            if not _looks_like_address(str(smtp.get("from_address") or "")):
                raise ValueError("启用前请填写正确的发件邮箱")
        smtp["enabled"] = enabled
    save(data)
    return smtp_public()
