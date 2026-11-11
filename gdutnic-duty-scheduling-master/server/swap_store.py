"""Duty swaps agreed by two members: swap one person's attendance between two shifts.

A swap row records the two shifts, the two people involved, and when it was made.
While it exists, both shifts show the swapped assignment (person A appears on shift B
and vice versa). The row is deleted automatically once both shifts have finished and
both people have checked in, and may be deleted by hand until either shift starts.
"""
from datetime import datetime

from server import db, schedule_store, system_clock


def init(conn):
    if isinstance(conn, db.MysqlConnection):
        conn.execute(
            "CREATE TABLE IF NOT EXISTS swaps ("
            " id BIGINT PRIMARY KEY AUTO_INCREMENT,"
            " shift_a BIGINT NOT NULL, name_a VARCHAR(64) NOT NULL,"
            " shift_b BIGINT NOT NULL, name_b VARCHAR(64) NOT NULL,"
            " created_at VARCHAR(40) NOT NULL,"
            " UNIQUE KEY uniq_shift_a_name (shift_a, name_a),"
            " UNIQUE KEY uniq_shift_b_name (shift_b, name_b)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin")
    else:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS swaps ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " shift_a INTEGER NOT NULL, name_a TEXT NOT NULL,"
            " shift_b INTEGER NOT NULL, name_b TEXT NOT NULL,"
            " created_at TEXT NOT NULL,"
            " UNIQUE (shift_a, name_a), UNIQUE (shift_b, name_b))")


def _now():
    return system_clock.now()


def _shift(conn, shift_id):
    row = conn.execute('SELECT * FROM shifts WHERE id=?', (shift_id,)).fetchone()
    return dict(row) if row else None


def _attended(conn, shift_id, name):
    return conn.execute('SELECT 1 FROM attendance WHERE shift_id=? AND name=?',
                        (shift_id, name)).fetchone() is not None


def create(shift_a, name_a, shift_b, name_b, confirmed=False):
    """Register a swap; both people must already be on the shifts they leave."""
    if not confirmed:
        raise ValueError('请先勾选“双方已协商完成并同意调班”')
    if shift_a == shift_b or name_a == name_b:
        raise ValueError('请选择两个不同的班次和两位不同的人员')
    with db.write() as conn:
        init(conn)
        first, second = _shift(conn, shift_a), _shift(conn, shift_b)
        if not first or not second:
            raise LookupError('班次不存在')
        if not _attended(conn, shift_a, name_a):
            raise ValueError(f'{name_a} 不在所选班次中')
        if not _attended(conn, shift_b, name_b):
            raise ValueError(f'{name_b} 不在所选班次中')
        # Swapping a shift that already began would rewrite history.
        now = _now()
        for shift in (first, second):
            if schedule_store.shift_bounds(shift)[0] <= now:
                raise ValueError('只能在双方班次开始前调换')
        clash = conn.execute(
            'SELECT 1 FROM swaps WHERE (shift_a=? AND name_a=?) OR (shift_b=? AND name_b=?)'
            ' OR (shift_a=? AND name_a=?) OR (shift_b=? AND name_b=?)',
            (shift_a, name_a, shift_a, name_a, shift_b, name_b, shift_b, name_b)).fetchone()
        if clash:
            raise ValueError('该班次或人员已存在调班记录')
        swap_id = conn.execute(
            'INSERT INTO swaps(shift_a,name_a,shift_b,name_b,created_at) VALUES(?,?,?,?,?)',
            (shift_a, name_a, shift_b, name_b, now.isoformat(timespec='seconds'))).lastrowid
        _apply(conn, swap_id)
        return swap_id


def _apply(conn, swap_id):
    """Exchange the two people: each leaves their own shift and joins the other's."""
    row = conn.execute('SELECT * FROM swaps WHERE id=?', (swap_id,)).fetchone()
    if not row:
        return
    conn.execute('DELETE FROM attendance WHERE shift_id=? AND name=?', (row['shift_a'], row['name_a']))
    conn.execute('DELETE FROM attendance WHERE shift_id=? AND name=?', (row['shift_b'], row['name_b']))
    db.insert_missing(conn, 'attendance', {'shift_id': row['shift_b'], 'name': row['name_a']},
                      ['shift_id', 'name'])
    db.insert_missing(conn, 'attendance', {'shift_id': row['shift_a'], 'name': row['name_b']},
                      ['shift_id', 'name'])


def _restore(conn, row):
    """Undo a swap: put each person back on their original shift.

    Deletion is only allowed before either shift starts, so no attendance can be lost.
    """
    conn.execute('DELETE FROM attendance WHERE shift_id=? AND name=?', (row['shift_b'], row['name_a']))
    conn.execute('DELETE FROM attendance WHERE shift_id=? AND name=?', (row['shift_a'], row['name_b']))
    db.insert_missing(conn, 'attendance', {'shift_id': row['shift_a'], 'name': row['name_a']},
                      ['shift_id', 'name'])
    db.insert_missing(conn, 'attendance', {'shift_id': row['shift_b'], 'name': row['name_b']},
                      ['shift_id', 'name'])


def _done(conn, row, now):
    """Both swaps take effect on finished shifts where each person has checked in."""
    for shift_id, name in ((row['shift_a'], row['name_b']), (row['shift_b'], row['name_a'])):
        shift = _shift(conn, shift_id)
        if not shift or schedule_store.shift_bounds(shift)[1] > now:
            return False
        checked = conn.execute('SELECT checked_at FROM attendance WHERE shift_id=? AND name=?',
                               (shift_id, name)).fetchone()
        if not checked or not checked['checked_at']:
            return False
    return True


def _cleanup(conn):
    """Remove swaps whose work is complete, and drops of shifts that no longer exist."""
    now = _now()
    removed = []
    for row in conn.execute('SELECT * FROM swaps').fetchall():
        if not _shift(conn, row['shift_a']) or not _shift(conn, row['shift_b']):
            conn.execute('DELETE FROM swaps WHERE id=?', (row['id'],))
            removed.append(row['id'])
        elif _done(conn, row, now):
            conn.execute('DELETE FROM swaps WHERE id=?', (row['id'],))
            removed.append(row['id'])
    return removed


def list_swaps():
    """Active swaps with the shift details needed by the admin list."""
    with db.read() as conn:
        init(conn)
        _cleanup(conn)
        rows = conn.execute('SELECT * FROM swaps ORDER BY id').fetchall()
        result = []
        for row in rows:
            first, second = _shift(conn, row['shift_a']), _shift(conn, row['shift_b'])
            if not first or not second:
                continue
            now = _now()
            result.append({
                'id': row['id'],
                'created_at': row['created_at'],
                'a': {'shift': _pack(first, row['name_a']), 'person': row['name_a']},
                'b': {'shift': _pack(second, row['name_b']), 'person': row['name_b']},
                'deletable': min(schedule_store.shift_bounds(first)[0],
                                 schedule_store.shift_bounds(second)[0]) > now,
            })
        result.sort(key=lambda item: (item['a']['shift']['day'], item['a']['shift']['start']))
        return result


def _pack(shift, name):
    start, end = schedule_store.shift_bounds(shift)
    return {'id': shift['id'], 'day': shift['day'], 'start': shift['start'], 'end': shift['end'],
            'note': shift['note'], 'name': name,
            'start_at': start.isoformat(timespec='seconds'), 'end_at': end.isoformat(timespec='seconds')}


def delete(swap_id):
    with db.write() as conn:
        init(conn)
        row = conn.execute('SELECT * FROM swaps WHERE id=?', (swap_id,)).fetchone()
        if not row:
            raise LookupError('调班记录不存在')
        for shift_id in (row['shift_a'], row['shift_b']):
            shift = _shift(conn, shift_id)
            if shift and schedule_store.shift_bounds(shift)[0] <= _now():
                raise ValueError('已有班次开始，无法再删除该调班')
        _restore(conn, row)
        conn.execute('DELETE FROM swaps WHERE id=?', (swap_id,))


def candidates(name, shift_id):
    """A person's upcoming shifts for the swap picker.

    Not limited to the current week: any future shift not already involved in a swap
    may be exchanged, so a swap can span weeks or months.
    """
    with db.read() as conn:
        init(conn)
        now = _now()
        rows = conn.execute(
            "SELECT s.* FROM shifts s JOIN attendance a ON a.shift_id=s.id AND a.name=?"
            " ORDER BY s.day, s.start, s.id", (name,)).fetchall()
        # Any shift this person is already involved in a swap for is unavailable, as
        # either the giver or the receiver.
        swapped = set()
        for row in conn.execute('SELECT * FROM swaps').fetchall():
            if row['name_a'] == name or row['name_b'] == name:
                swapped.update((row['shift_a'], row['shift_b']))
        items = []
        for row in rows:
            shift = dict(row)
            if shift['id'] == shift_id or shift['id'] in swapped:
                continue
            if schedule_store.shift_bounds(shift)[0] <= now:
                continue
            items.append(_pack(shift, name))
        return items
