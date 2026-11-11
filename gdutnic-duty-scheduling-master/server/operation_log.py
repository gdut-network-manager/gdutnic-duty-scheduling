"""Operation log: who did what, and when.

Every meaningful write records one row here. The administrator page reads them back,
newest first. The table is created lazily so older databases upgrade themselves.

The table is capped by size, not by row count: once it grows past LIMIT_BYTES the
oldest rows are deleted until it is back down to KEEP_BYTES. That keeps a long-running
deployment from filling the database with request history while still leaving a usable
window of recent activity.
"""
from __future__ import annotations

from datetime import datetime

from server import db

TABLE = 'operation_log'

# Roll the log at 10 MB and trim back down to 5 MB, so trimming runs rarely and each
# run frees half the table instead of the newest rows being churned every write.
LIMIT_BYTES = 10 * 1024 * 1024
KEEP_BYTES = 5 * 1024 * 1024
# Recheck the size every N inserts: measuring the table on every write would dominate
# the cost of writing one row.
CHECK_EVERY = 200
_inserts = 0


def _row_bytes(conn):
    """Approximate stored size of one row on both backends."""
    return conn.execute(
        'SELECT COALESCE(SUM(LENGTH(at)+LENGTH(actor)+LENGTH(role)+LENGTH(action)+LENGTH(detail)),0) AS n'
        ' FROM operation_log').fetchone()['n']


def init(conn):
    if isinstance(conn, db.MysqlConnection):
        conn.execute(
            "CREATE TABLE IF NOT EXISTS operation_log ("
            " id BIGINT PRIMARY KEY AUTO_INCREMENT,"
            " at VARCHAR(40) NOT NULL, actor VARCHAR(64) NOT NULL,"
            " role VARCHAR(16) NOT NULL DEFAULT '', action VARCHAR(64) NOT NULL,"
            " detail TEXT NOT NULL,"
            " INDEX idx_operation_log_at (at)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin")
    else:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS operation_log ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " at TEXT NOT NULL, actor TEXT NOT NULL, role TEXT NOT NULL DEFAULT '',"
            " action TEXT NOT NULL, detail TEXT NOT NULL)")
        conn.execute('CREATE INDEX IF NOT EXISTS idx_operation_log_at ON operation_log(at)')


def _clock():
    from server import system_clock
    return system_clock.now()


def prune(conn=None):
    """Delete the oldest rows until the table is back below KEEP_BYTES.

    Returns the number of rows removed. Safe to call at any time; when no connection is
    supplied it opens its own write transaction.
    """
    if conn is not None:
        return _prune(conn)
    with db.write() as own:
        return _prune(own)


def _prune(conn):
    used = _row_bytes(conn)
    if used <= LIMIT_BYTES:
        return 0
    # Walk forward from the oldest rows, accumulating their size, and delete exactly the
    # prefix needed to get under KEEP_BYTES. Doing it by measured size rather than by a
    # row count keeps the cap meaningful even when rows differ greatly in `detail` size.
    removed = 0
    target = used - KEEP_BYTES
    freed = 0
    last_id = None
    while freed < target:
        batch = conn.execute(
            'SELECT id, LENGTH(at)+LENGTH(actor)+LENGTH(role)+LENGTH(action)+LENGTH(detail) AS n'
            ' FROM operation_log' + (' WHERE id>?' if last_id is not None else '')
            + ' ORDER BY id LIMIT 500', (last_id,) if last_id is not None else ()).fetchall()
        if not batch:
            break
        cut = []
        for row in batch:
            cut.append(row['id'])
            freed += row['n'] or 0
            if freed >= target:
                break
        last_id = cut[-1]
        removed += conn.execute('DELETE FROM operation_log WHERE id<=? AND id>=?',
                                (cut[-1], cut[0])).rowcount
    return removed


def record(actor, action, detail='', role=''):
    """Append one log line. Never raises: logging must not break the operation."""
    global _inserts
    try:
        with db.write() as conn:
            init(conn)
            conn.execute(
                'INSERT INTO operation_log(at,actor,role,action,detail) VALUES(?,?,?,?,?)',
                (_clock().isoformat(timespec='seconds'), str(actor or '匿名')[:64],
                 str(role or '')[:16], str(action)[:64], str(detail or '')[:2000]))
            # Size is checked periodically instead of on every write.
            _inserts += 1
            if _inserts >= CHECK_EVERY:
                _inserts = 0
                _prune(conn)
    except Exception as exc:  # pragma: no cover - defensive
        print(f'[log] 写入操作日志失败：{type(exc).__name__}: {exc}')


def list_entries(limit=300, actor='', action=''):
    """Newest first, optionally filtered by account or action keyword."""
    limit = max(1, min(int(limit), 1000))
    with db.read() as conn:
        init(conn)
        sql = 'SELECT at,actor,role,action,detail FROM operation_log'
        args = []
        clauses = []
        if actor:
            clauses.append('actor=?')
            args.append(actor)
        if action:
            clauses.append('(action LIKE ? OR detail LIKE ?)')
            args.extend([f'%{action}%', f'%{action}%'])
        if clauses:
            sql += ' WHERE ' + ' AND '.join(clauses)
        sql += ' ORDER BY id DESC LIMIT ?'
        args.append(limit)
        rows = conn.execute(sql, args).fetchall()
    return [dict(row) for row in rows]


def stats():
    """Current size of the audit trail and the cap it is kept under, for the page header."""
    with db.read() as conn:
        init(conn)
        used = _row_bytes(conn)
        rows = conn.execute('SELECT COUNT(*) AS n FROM operation_log').fetchone()['n']
    return {'used_bytes': used, 'rows': rows,
            'limit_bytes': LIMIT_BYTES, 'keep_bytes': KEEP_BYTES}


def actors():
    with db.read() as conn:
        init(conn)
        return [row['actor'] for row in conn.execute(
            'SELECT DISTINCT actor FROM operation_log ORDER BY actor').fetchall()]
