"""Idle limits and revocable, server-side login leases.

Only the explicit interaction heartbeat advances a lease; API polling does not.
"""
import asyncio
import math
import secrets
import time
from server import db

DEFAULTS = {'member_minutes': 120, 'admin_minutes': 30,
            'member_response_seconds': 90, 'admin_response_seconds': 90}
KEY = 'session_idle'


def settings():
    with db.read() as conn:
        value = db.get_setting(conn, KEY, {})
    value = value if isinstance(value, dict) else {}
    return {key: value.get(key) if type(value.get(key)) is int and
            (30 <= value[key] <= 3600 if key.endswith('_seconds') else 1 <= value[key] <= 240) else default
            for key, default in DEFAULTS.items()}


def response_timeout(role):
    return settings()[role + '_response_seconds']


def save(body):
    if not isinstance(body, dict):
        raise ValueError('请填写自动退出设置')
    value = {}
    previous = settings()
    for key in DEFAULTS:
        number = body.get(key, previous[key]) if key.endswith('_seconds') else body.get(key)
        valid = type(number) is int and (30 <= number <= 3600 if key.endswith('_seconds') else 1 <= number <= 240)
        if not valid:
            raise ValueError('无响应时限须为 30～3600 秒的整数' if key.endswith('_seconds') else '无操作时限须为 1～240 分钟的整数')
        value[key] = number
    now = time.time()
    with db.write() as conn:
        db.set_setting(conn, KEY, value)
        conn.execute('DELETE FROM login_sessions WHERE expires_at<=?', (now,))
        for role in ('member', 'admin'):
            conn.execute('UPDATE login_sessions SET expires_at=last_activity+? WHERE kind=?',
                         (value[role + '_minutes'] * 60, role))
    return value


def create(role, username, *, sso_identity=''):
    now = time.time()
    lifetime = settings()[role + '_minutes'] * 60
    handle = secrets.token_hex(32)
    with db.write() as conn:
        conn.execute('DELETE FROM login_sessions WHERE expires_at<=?', (now,))
        conn.execute('INSERT INTO login_sessions(id,kind,username,last_activity,expires_at,sso_identity,last_response) VALUES(?,?,?,?,?,?,?)',
                     (handle, role, username, now, now + lifetime, sso_identity, now))
    return handle


def state(handle, role, username=None):
    if not isinstance(handle, str) or len(handle) != 64:
        return None
    with db.read() as conn:
        row = conn.execute('SELECT * FROM login_sessions WHERE id=? AND kind=?', (handle, role)).fetchone()
    if not row or (username is not None and row['username'] != username):
        return None
    deadline = min(row['expires_at'], row['last_activity'] + settings()[role + '_minutes'] * 60)
    if time.time() >= deadline or (row['last_response'] and time.time() - row['last_response'] >= response_timeout(role)):
        revoke(handle)
        return None
    return {**dict(row), 'expires_at': deadline}


def touch(handle, role, username, activity_at=None):
    # Recheck inside the transaction so delayed input cannot resurrect an expired lease.
    now = time.time()
    lifetime = settings()[role + '_minutes'] * 60
    if activity_at is None:
        activity_at = now
    if isinstance(activity_at, bool) or not isinstance(activity_at, (int, float)) or not math.isfinite(activity_at):
        raise ValueError('操作时间格式不正确')
    with db.write() as conn:
        row = conn.execute('SELECT * FROM login_sessions WHERE id=? AND kind=? AND username=?',
                           (handle, role, username)).fetchone()
        if not row or now >= min(row['expires_at'], row['last_activity'] + lifetime) or (row['last_response'] and now - row['last_response'] >= response_timeout(role)):
            return False
        # Old or out-of-order heartbeats do not prolong a session.
        if activity_at >= now - 60:
            stamp = max(row['last_activity'], min(now, activity_at))
            conn.execute('UPDATE login_sessions SET last_activity=?,expires_at=? WHERE id=?',
                         (stamp, stamp + lifetime, handle))
    return True


def revoke(handle):
    if isinstance(handle, str) and handle:
        with db.write() as conn:
            conn.execute('DELETE FROM login_sessions WHERE id=?', (handle,))


def heartbeat(handle, role, username):
    """Responsiveness is independent of interaction: never extend the idle deadline."""
    if not state(handle, role, username):
        return False
    with db.write() as conn:
        now = time.time()
        return bool(conn.execute('UPDATE login_sessions SET last_response=? WHERE id=? AND kind=? AND username=? AND expires_at>? AND (last_response=0 OR last_response>?)',
                                  (now, handle, role, username, now, now - response_timeout(role))).rowcount)


def reap_unresponsive():
    limits = settings()
    with db.write() as conn:
        # Older leases get one grace window when the heartbeat feature is deployed.
        conn.execute('UPDATE login_sessions SET last_response=? WHERE last_response=0', (time.time(),))
        removed = 0
        for role in ('member', 'admin'):
            removed += conn.execute('DELETE FROM login_sessions WHERE kind=? AND last_response>0 AND last_response<=?',
                                    (role, time.time() - limits[role + '_response_seconds'])).rowcount
        return removed


async def presence_worker():
    while True:
        try:
            reap_unresponsive()
        except Exception as exc:
            print(f'[session] 心跳清理暂时失败：{type(exc).__name__}')
        await asyncio.sleep(15)
