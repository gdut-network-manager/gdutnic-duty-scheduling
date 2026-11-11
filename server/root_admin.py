"""The 原始管理员: one built-in account created on first setup.

Its credentials are kept in ``data/root_admin.json`` (mode 0600), outside the database,
so they survive a database reset and stay out of every SQL-backed listing. The operator
may choose the name on the first setup page (blank means the built-in one, kept in
``USERNAME``), but once created the account cannot be renamed, disabled or deleted, and
it always holds every permission — checks here never consult the editable table.
"""
from __future__ import annotations

import json
import os
import secrets
import tempfile
from datetime import datetime

from server import admin_store, db

USERNAME = '原始管理员'
FILE = 'root_admin.json'

# Mirrors permissions.PERMISSIONS; duplicated on purpose so the root account keeps the
# full set even if the editable tree changes or a stray write damages its row.
ALL_PERMISSIONS = ('duty', 'member_field', 'field_confirm', 'overview', 'weekly', 'special',
                   'swap', 'members', 'field', 'settings', 'logs', 'data', 'permission_groups',
                   'attendance_edit', 'rules', 'clock', 'smtp', 'push', 'sso', 'checkin_manage')

_generated = None


def path():
    return db.DATA / FILE


def _load():
    """The stored record, or None before first setup."""
    try:
        data = json.loads(path().read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get('username') or not data.get('password'):
        return None
    return data


def _save(data):
    """Write atomically with owner-only permissions; the file holds a password hash."""
    path().parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path().parent, prefix='.root_admin-')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        os.chmod(temp, 0o600)
        os.replace(temp, path())
    except Exception:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
    return data


def exists():
    return _load() is not None


def username():
    data = _load()
    return str(data['username']) if data else USERNAME


def is_root(name):
    if not name:
        return False
    data = _load()
    # Before first setup the default name still reserves the identity.
    return str(name) == (str(data['username']) if data else USERNAME)


def clean_username(value):
    """Validate an operator-chosen root name, or fall back to the built-in one.

    The name is stored in root_admin.json and compared verbatim on every login, so it
    only needs to be a sensible display name; it never enters a SQL statement.
    """
    name = str(value or '').strip()
    if not name:
        return USERNAME
    if len(name) > 64:
        raise ValueError('用户名最多 64 个字')
    if any(ord(char) < 32 for char in name):
        raise ValueError('用户名不能包含控制字符')
    return name


def create_if_missing(password=None, username=None):
    """Create the account once; later calls never touch an existing record."""
    data = _load()
    if data:
        return {'created': False, 'username': str(data['username'])}
    name = clean_username(username)
    password = password or secrets.token_urlsafe(18)
    _save({'username': name,
           'password': admin_store._hash_password(password),
           'created_at': datetime.now().astimezone().isoformat(timespec='seconds')})
    return {'created': True, 'username': name}


def bootstrap_existing():
    """Upgrade path: an installed site starts with a root account on next boot.

    The generated password is printed once to the server console; the operator signs in
    with it and rotates it. Nothing is reused from existing accounts.
    """
    global _generated
    if exists():
        return None
    password = secrets.token_urlsafe(18)
    create_if_missing(password)
    _generated = password
    return password


def take_generated():
    """The password created by `bootstrap_existing`, so the caller can print it once."""
    global _generated
    value, _generated = _generated, None
    return value


def verify(password):
    data = _load()
    return bool(data) and admin_store._verify_password(str(password or ''), str(data['password']))


def change_password(new_password):
    if len(new_password) < 6 or len(new_password) > 128:
        raise ValueError('密码长度应为 6～128 位')
    data = _load()
    if not data:
        raise LookupError('原始管理员尚未创建')
    data['password'] = admin_store._hash_password(new_password)
    data['updated_at'] = datetime.now().astimezone().isoformat(timespec='seconds')
    _save(data)
    return {'username': str(data['username'])}


def purge_mirror_row(conn=None):
    """Delete any row the root identity may have left in `member_accounts`.

    The root never lives in the account table. Older builds created a mirror row there;
    this removes it so 用户管理 和 数据管理 都不再看到这个账号。Idempotent and safe to call
    on every boot.
    """
    data = _load()
    if not data:
        return 0
    own = conn is None
    context = db.write() if own else None
    if own:
        conn = context.__enter__()
    try:
        return conn.execute('DELETE FROM member_accounts WHERE username=?',
                            (data['username'],)).rowcount
    finally:
        if own:
            context.__exit__(None, None, None)


def session_version():
    """Stable per-password token, so changing the password ends older sessions."""
    data = _load()
    if not data:
        return ''
    import hashlib
    return hashlib.sha256(str(data['password']).encode()).hexdigest()


def effective_permissions(name):
    """The root account always resolves to every permission."""
    return set(ALL_PERMISSIONS) if is_root(name) else None
