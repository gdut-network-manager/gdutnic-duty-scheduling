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
            "smtp": db.get_setting(conn, "smtp", {}) or {},
        }
    dirty = False
    if not isinstance(data.get("path"), str) or len(data["path"]) != 8:
        data["path"] = _new_path()
        dirty = True
    if not isinstance(data.get("smtp"), dict):
        data["smtp"] = {}
        dirty = True
    for section in ("smtp",):
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
