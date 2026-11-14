"""Monthly field-duty (外勤) registrations, one record per member per month."""
from datetime import date, datetime

from server import db, permissions, schedule_store, system_clock

TABLE = 'field_duty'


# 0 = 未登记, 1 = 已登记待确认, 2 = 已确认。只有 2 计入后台统计。
NONE, PENDING, CONFIRMED = 0, 1, 2
STATUS_TEXT = {NONE: '未登记', PENDING: '等待确认', CONFIRMED: '已确认'}


def init(conn):
    if isinstance(conn, db.MysqlConnection):
        conn.execute(
            "CREATE TABLE IF NOT EXISTS field_duty ("
            " month CHAR(7) NOT NULL, username VARCHAR(64) NOT NULL,"
            " registered_at VARCHAR(40) NOT NULL, manual TINYINT NOT NULL DEFAULT 0,"
            " status TINYINT NOT NULL DEFAULT 2,"
            " PRIMARY KEY(month, username)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin")
    else:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS field_duty ("
            " month TEXT NOT NULL, username TEXT NOT NULL, registered_at TEXT NOT NULL,"
            " manual INTEGER NOT NULL DEFAULT 0, status INTEGER NOT NULL DEFAULT 2,"
            " PRIMARY KEY(month, username))")
    db.upgrade_table(TABLE)


def _status(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return NONE


def _month(now=None):
    return (now or system_clock.now()).strftime('%Y-%m')


def registered(username, month=None):
    """True once the person has registered (pending or confirmed)."""
    return member_status(username, month)['status'] != NONE


def member_status(username, month=None):
    month = month or _month()
    with db.read() as conn:
        init(conn)
        row = conn.execute(
            'SELECT registered_at, status FROM field_duty WHERE month=? AND username=?',
            (month, username)).fetchone()
    status = _status(row['status']) if row else NONE
    return {'month': month, 'status': status, 'label': STATUS_TEXT.get(status, '未登记'),
            'registered': status != NONE,
            'registered_at': row['registered_at'] if row else None}


def register(username, month=None):
    """Member self-registration: 0 → 1 only; never confirms and never repeats."""
    month = month or _month()
    with db.write() as conn:
        init(conn)
        row = conn.execute(
            'SELECT registered_at, status FROM field_duty WHERE month=? AND username=?',
            (month, username)).fetchone()
        if row and _status(row['status']) != NONE:
            return {**member_status(username, month), 'created': False}
        stamp = system_clock.now().isoformat(timespec='seconds')
        if row:
            conn.execute('UPDATE field_duty SET registered_at=?, status=? WHERE month=? AND username=?',
                         (stamp, PENDING, month, username))
        else:
            conn.execute('INSERT INTO field_duty(month, username, registered_at, status)'
                         ' VALUES(?,?,?,?)', (month, username, stamp, PENDING))
    return {**member_status(username, month), 'created': True}


def confirm(username, month=None, *, actor):
    """A different member confirms 1 → 2; a caller identity is mandatory."""
    username, actor = str(username).strip(), str(actor or '').strip()
    if not actor:
        raise PermissionError('确认外勤必须指定操作账号')
    if actor == username:
        raise PermissionError('不能确认本人的外勤登记，请由其他成员确认')
    month = month or _month()
    with db.write() as conn:
        init(conn)
        row = conn.execute('SELECT status FROM field_duty WHERE month=? AND username=?',
                           (month, username)).fetchone()
        if not row or _status(row['status']) == NONE:
            raise LookupError('该人员本月没有待确认的外勤登记')
        if _status(row['status']) != CONFIRMED:
            conn.execute('UPDATE field_duty SET status=? WHERE month=? AND username=?',
                         (CONFIRMED, month, username))
    return member_status(username, month)


def reject_pending(username, month=None):
    """The member confirmation permission only permits rejecting pending rows."""
    month = month or _month()
    with db.write() as conn:
        init(conn)
        deleted = conn.execute(
            'DELETE FROM field_duty WHERE month=? AND username=? AND status=?',
            (month, username, PENDING)).rowcount
        if not deleted:
            raise LookupError('该人员本月没有待确认的外勤登记')
    return {'month': month, 'username': username, 'registered': False,
            'registered_at': None, 'manual': False}


def pending(month=None, *, exclude_username=None):
    """Registrations waiting for confirmation (status 1), oldest first."""
    month = month or _month()
    with db.read() as conn:
        init(conn)
        query = 'SELECT username, registered_at FROM field_duty WHERE month=? AND status=?'
        args = [month, PENDING]
        if exclude_username is not None:
            query += ' AND username<>?'
            args.append(exclude_username)
        rows = conn.execute(query + ' ORDER BY registered_at, username', args).fetchall()
    return {'month': month,
            'items': [{'username': r['username'], 'registered_at': r['registered_at']} for r in rows]}


def month_span(now=None):
    """Months that hold a field-duty record, always including the current month.

    The board's arrows stop at these bounds so the buttons can be disabled when
    stepping further back or forward would show nothing new.
    """
    month = _month(now)
    with db.read() as conn:
        init(conn)
        row = conn.execute('SELECT MIN(month) AS first, MAX(month) AS last FROM field_duty').fetchone()
    months = [value for value in ((row['first'] if row else None), (row['last'] if row else None)) if value]
    return {'first': min([*months, month]), 'last': max([*months, month]), 'current': month}


def _eligible_accounts(conn):
    return [row['username'] for row in conn.execute(
        'SELECT username FROM member_accounts WHERE active=1 ORDER BY username')
        if permissions.has(row['username'], 'member_field')]


def overview(month=None):
    """Accounts with member-side field access and their monthly state."""
    month = month or _month()
    with db.read() as conn:
        init(conn)
        rows = conn.execute(
            'SELECT username, registered_at, manual, status FROM field_duty WHERE month=?',
            (month,)).fetchall()
        # Only confirmed records (status 2) count towards the monthly board.
        done = {row['username']: (row['registered_at'], bool(row['manual']))
                for row in rows if _status(row['status']) == CONFIRMED}
        accounts = _eligible_accounts(conn)
    items = [{'username': name, 'registered': name in done,
              'registered_at': done[name][0] if name in done else None,
              'manual': done[name][1] if name in done else False}
             for name in accounts]
    items.sort(key=lambda item: (not item['registered'], item['username']))
    span = month_span()
    # Deleting a month must clear pending rows too, so report every stored row, not
    # just the confirmed ones the board counts.
    stored = len(rows)
    pending = sum(1 for row in rows if _status(row['status']) == PENDING)
    return {'month': month, 'registered': sum(item['registered'] for item in items), 'total': len(items), 'items': items,
            'stored': stored, 'pending': pending,
            'current': span['current'], 'first': span['first'], 'last': span['last'],
            'previous': _shift_month(month, -1) if month > span['first'] else None,
            'next': _shift_month(month, 1) if month < span['last'] else None}


def _shift_month(month, delta):
    """Move a `YYYY-MM` string by `delta` months."""
    first = date(int(month[:4]), int(month[5:7]), 1)
    total = first.year * 12 + first.month - 1 + delta
    return f'{total // 12:04d}-{total % 12 + 1:02d}'


def month_summary(month=None):
    """Counts for a month, used by the board and its delete confirmation."""
    month = month or _month()
    with db.read() as conn:
        init(conn)
        rows = conn.execute(
            'SELECT status, COUNT(*) AS n FROM field_duty WHERE month=? GROUP BY status',
            (month,)).fetchall()
        accounts = _eligible_accounts(conn)
    counts = {_status(row['status']): row['n'] for row in rows}
    confirmed = counts.get(CONFIRMED, 0)
    return {'month': month, 'total': len(accounts), 'registered': sum(counts.values()),
            'pending': counts.get(PENDING, 0), 'confirmed': confirmed,
            'none': counts.get(NONE, 0)}


def clear_month(month=None):
    """Delete every field-duty record for one month.

    Registrations are not archival data: the board is a per-month status, so removing a
    month simply resets it to “未登记”. Returns what was removed so the log can say so.
    """
    month = month or _month()
    summary = month_summary(month)
    with db.write() as conn:
        init(conn)
        deleted = conn.execute('DELETE FROM field_duty WHERE month=?', (month,)).rowcount
    return {**summary, 'deleted': deleted}


def set_participation(username, registered, month=None):
    """Administrator override: mark someone as having joined (or not) for the month."""
    month = month or _month()
    with db.write() as conn:
        init(conn)
        row = conn.execute('SELECT registered_at FROM field_duty WHERE month=? AND username=?',
                           (month, username)).fetchone()
        if registered:
            # An administrator marking presence counts as confirmed (status 2).
            if row:
                conn.execute('UPDATE field_duty SET manual=1, status=? WHERE month=? AND username=?',
                             (CONFIRMED, month, username))
                return {'month': month, 'username': username, 'registered': True,
                        'registered_at': row['registered_at'], 'manual': True}
            stamp = system_clock.now().isoformat(timespec='seconds')
            conn.execute('INSERT INTO field_duty(month, username, registered_at, manual, status)'
                         ' VALUES(?,?,?,1,?)', (month, username, stamp, CONFIRMED))
            return {'month': month, 'username': username, 'registered': True,
                    'registered_at': stamp, 'manual': True}
        conn.execute('DELETE FROM field_duty WHERE month=? AND username=?', (month, username))
        return {'month': month, 'username': username, 'registered': False,
                'registered_at': None, 'manual': False}
