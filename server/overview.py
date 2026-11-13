"""Monthly attendance statistics and weekly status for the admin overview."""
from datetime import date, datetime, timedelta

from pypinyin import lazy_pinyin

from server import db, duty_settings, schedule_store, system_clock

STATUS_LABELS = {'normal': '正常', 'late': '迟到', 'absent': '旷班', 'pending': '待签到', 'unconfirmed': '待确认'}
# Statuses an administrator may set by hand from the overview card.
MANUAL_STATUSES = ('normal', 'late', 'absent')


def member_status(shift, member, now, rules):
    """Read the recorded decision; timestamps and current rules never rewrite history."""
    override = str(member.get('status_override') or '')
    if override in MANUAL_STATUSES:
        return override
    status = str(member.get('final_status') or '')
    if status in MANUAL_STATUSES:
        return status
    return 'unconfirmed' if member.get('checked_at') else 'pending'


def _attendance_by_shift(conn, rows):
    """One query for every shift's attendance instead of one per shift."""
    ids = [row['id'] for row in rows]
    grouped = {}
    if not ids:
        return grouped
    placeholders = ','.join('?' for _ in ids)
    for record in conn.execute(
            f'SELECT shift_id,name,checked_at,status_override,final_status FROM attendance WHERE shift_id IN ({placeholders})'
            ' ORDER BY shift_id,name', ids):
        grouped.setdefault(record['shift_id'], []).append(
            {'name': record['name'], 'checked_at': record['checked_at'],
             'status_override': record['status_override'] or '', 'final_status': record['final_status'] or ''})
    return grouped


def _shift_payload(conn, row, now, rules, attendance=None):
    members = [dict(m) for m in (attendance.get(row['id'], []) if attendance is not None else
               conn.execute('SELECT name,checked_at,status_override,final_status FROM attendance WHERE shift_id=? ORDER BY name',
                            (row['id'],)))]
    for member in members:
        member['status'] = member_status(row, member, now, rules)
    start, end = schedule_store.shift_bounds(row)
    if now < start:
        phase = 'future'
    elif now < end:
        phase = 'current'
    else:
        phase = 'past'
    item = dict(row)
    item.update(
        members=members,
        late=[m['name'] for m in members if m['status'] == 'late'],
        absent=[m['name'] for m in members if m['status'] == 'absent'],
        phase=phase,
    )
    return item


def _month_bounds(month):
    first = date.fromisoformat(str(month) + '-01')
    following = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, following - timedelta(days=1)


def _week_start(day):
    """Monday of the week containing `day`."""
    return day - timedelta(days=day.weekday())


def parse_week(week=None, now=None):
    """Accepts a `YYYY-MM-DD` anchor anywhere in the week; defaults to the current one."""
    now = system_clock.now() if now is None else now
    if not week:
        return _week_start(now.date())
    return _week_start(date.fromisoformat(str(week)))


def week_span(now=None):
    """Earliest and latest week that hold a shift, always including the current week.

    Arrows stop at these bounds, so a week with no shifts can still be stepped through
    instead of stranding the user in the middle of a gap.
    """
    now = system_clock.now() if now is None else now
    current = _week_start(now.date())
    with db.read() as conn:
        schedule_store.init(conn)
        row = conn.execute('SELECT MIN(day) AS first, MAX(day) AS last FROM shifts').fetchone()
    days = [date.fromisoformat(value) for value in
            (row['first'] if row else None, row['last'] if row else None) if value]
    earliest = min([*days, current])
    latest = max([*days, current])
    return {'first': _week_start(earliest).isoformat(), 'last': _week_start(latest).isoformat(),
            'current': current.isoformat()}


def overview(month=None, now=None, week=None):
    now = system_clock.now() if now is None else now
    rules = duty_settings.load()
    schedule_store.finalize_absences(now)
    first, last = _month_bounds(month or now.strftime('%Y-%m'))
    week_start = parse_week(week, now)
    week_end = week_start + timedelta(days=6)

    normal = late = absent = total = 0
    month_items = []
    all_items = []
    with db.read() as conn:
        schedule_store.init(conn)
        rows = conn.execute(
            'SELECT s.*,w.slot AS weekly_slot FROM shifts s '
            'LEFT JOIN weekly_instances w ON w.shift_id=s.id '
            'WHERE (s.day>=? AND s.day<=?) OR (s.day>=? AND s.day<=?) '
            'ORDER BY s.day,s.start,s.id',
            (first.isoformat(), last.isoformat(),
             week_start.isoformat(), week_end.isoformat())).fetchall()
        attendance = _attendance_by_shift(conn, rows)
        for row in rows:
            item = _shift_payload(conn, row, now, rules, attendance)
            all_items.append(item)
            if first.isoformat() <= item['day'] <= last.isoformat():
                month_items.append(item)
                # Only shifts that have already started count towards the month's rate.
                if schedule_store.shift_bounds(row)[0] <= now:
                    for member in item['members']:
                        if member['status'] not in MANUAL_STATUSES:
                            continue
                        total += 1
                        if member['status'] == 'normal':
                            normal += 1
                        elif member['status'] == 'late':
                            late += 1
                        elif member['status'] == 'absent':
                            absent += 1
        # Archived check-ins (schedules cleared) still count for the month.
        live = {(item['day'], item['start'], m['name'], m['checked_at'] or '')
                for item in month_items for m in item['members']}
        for row in conn.execute(
                'SELECT day,start,end,name,checked_at,final_status FROM attendance_history'
                ' WHERE day>=? AND day<=?', (first.isoformat(), last.isoformat())).fetchall():
            if (row['day'], row['start'], row['name'], row['checked_at'] or '') in live:
                continue
            shift = {'day': row['day'], 'start': row['start'], 'end': row['end']}
            if schedule_store.shift_bounds(shift)[0] > now:
                continue
            member = {'name': row['name'], 'checked_at': row['checked_at'], 'final_status': row['final_status']}
            status = member_status(shift, member, now, rules)
            if status not in MANUAL_STATUSES:
                continue
            total += 1
            if status == 'normal':
                normal += 1
            elif status == 'late':
                late += 1
            elif status == 'absent':
                absent += 1
        timeline = []
        today = now.date().isoformat()
        previous = (now.date() - timedelta(days=1)).isoformat()
        timeline_rows = conn.execute(
            "SELECT * FROM shifts WHERE day=? OR (day=? AND end < start AND end > '00:00')"
            " ORDER BY day,start,id", (today, previous)).fetchall()
        timeline_attendance = _attendance_by_shift(conn, timeline_rows)
        for row in timeline_rows:
            timeline.append(_shift_payload(conn, row, now, rules, timeline_attendance))

    days = []
    for offset in range(7):
        current = week_start + timedelta(days=offset)
        days.append({
            'day': current.isoformat(),
            'weekday': current.weekday(),
            'shifts': _range_items(all_items, current),
        })
    span = week_span(now=now)
    return {
        'month': first.strftime('%Y-%m'),
        'today': now.date().isoformat(),
        'rules': rules,
        'stats': {
            'normal': normal,
            'total': total,
            'late': late,
            'absent': absent,
            'rate': round(normal / total * 100, 1) if total else None,
        },
        'timeline': timeline,
        'week': {'start': week_start.isoformat(), 'end': week_end.isoformat(), 'days': days,
                 'current': span['current'], 'first': span['first'], 'last': span['last'],
                 # Navigation targets, so the client never has to parse dates.
                 'previous': (week_start - timedelta(days=7)).isoformat()
                 if week_start > date.fromisoformat(span['first']) else None,
                 'next': (week_start + timedelta(days=7)).isoformat()
                 if week_start < date.fromisoformat(span['last']) else None,
                 # Totals for the delete confirmation; the client never counts rows itself.
                 'shifts': sum(len(day['shifts']) for day in days),
                 'people': sum(len(shift['members']) for day in days for shift in day['shifts']),
                 'checked': sum(1 for day in days for shift in day['shifts']
                                for member in shift['members'] if member['checked_at'])},
    }


def pinyin_key(name):
    """Sort key: full pinyin of the name, falling back to the raw text."""
    return ''.join(lazy_pinyin(str(name or ''))).lower(), str(name or '')


def anomaly_stats(weeks=2, now=None):
    """Late/absent counts per person over the last `weeks` rolling weeks.

    The window ends now and starts `weeks * 7` days back; when the data does not reach
    that far, whatever falls inside the window is counted. Ranking breaks ties by name
    pinyin, so equal counts read alphabetically.
    """
    weeks = 2 if int(weeks) == 2 else 4
    now = system_clock.now() if now is None else now
    rules = duty_settings.load()
    schedule_store.finalize_absences(now)
    start_day = (now.date() - timedelta(days=weeks * 7 - 1)).isoformat()
    end_day = now.date().isoformat()
    late = {}
    absent = {}
    with db.read() as conn:
        schedule_store.init(conn)
        rows = conn.execute(
            'SELECT * FROM shifts WHERE day>=? AND day<=?', (start_day, end_day)).fetchall()
        attendance = _attendance_by_shift(conn, rows)
        seen = set()
        for row in rows:
            # Only finished (or at least started) duties can be judged.
            if schedule_store.shift_bounds(row)[0] > now:
                continue
            for member in attendance.get(row['id'], []):
                status = member_status(row, member, now, rules)
                seen.add((row['day'], row['start'], member['name'], member['checked_at'] or ''))
                if status == 'late':
                    late[member['name']] = late.get(member['name'], 0) + 1
                elif status == 'absent':
                    absent[member['name']] = absent.get(member['name'], 0) + 1
        # Archived check-ins from cleared schedules count too.
        for row in conn.execute(
                'SELECT day,start,end,name,checked_at,final_status FROM attendance_history'
                ' WHERE day>=? AND day<=?', (start_day, end_day)).fetchall():
            if (row['day'], row['start'], row['name'], row['checked_at'] or '') in seen:
                continue
            shift = {'day': row['day'], 'start': row['start'], 'end': row['end']}
            if schedule_store.shift_bounds(shift)[0] > now:
                continue
            status = member_status(shift, {'checked_at': row['checked_at'], 'final_status': row['final_status']}, now, rules)
            if status == 'late':
                late[row['name']] = late.get(row['name'], 0) + 1
            elif status == 'absent':
                absent[row['name']] = absent.get(row['name'], 0) + 1

    def ranking(counts):
        items = [{'name': name, 'count': count} for name, count in counts.items() if count]
        items.sort(key=lambda item: (-item['count'], pinyin_key(item['name'])))
        return items

    return {'weeks': weeks, 'start': start_day, 'end': end_day,
            'late': ranking(late), 'absent': ranking(absent)}


def _range_items(items, day):
    return [item for item in items if item['day'] == day.isoformat()]


def set_member_status(shift_id, name, status):
    """Administrator override for one person's attendance status."""
    if status not in MANUAL_STATUSES:
        raise ValueError('考勤状态只能设为准时、迟到或旷班')
    with db.write() as conn:
        row = conn.execute('SELECT name FROM attendance WHERE shift_id=? AND name=?',
                           (shift_id, name)).fetchone()
        if not row:
            raise LookupError('未找到该班次或排班人员')
        conn.execute('UPDATE attendance SET status_override=?,final_status=? WHERE shift_id=? AND name=?',
                     (status, status, shift_id, name))
    return {'shift_id': shift_id, 'name': name, 'status': status}


def member_schedule_week(week=None):
    """Public roster details for signed-in members; never return attendance data."""
    now = system_clock.now()
    start = parse_week(week, now)
    end = start + timedelta(days=6)
    span = week_span(now)
    with db.read() as conn:
        rows = [dict(row) for row in conn.execute(
            'SELECT s.id,s.day,s.start,s.end,s.location,s.note,s.special,w.slot AS weekly_slot '
            'FROM shifts s LEFT JOIN weekly_instances w ON w.shift_id=s.id '
            'WHERE s.day>=? AND s.day<=? ORDER BY s.day,s.start,s.id',
            (start.isoformat(), end.isoformat()))]
        names = {}
        if rows:
            ids = [row['id'] for row in rows]
            placeholders = ','.join('?' for _ in ids)
            for row in conn.execute(
                    f'SELECT shift_id,name FROM attendance WHERE shift_id IN ({placeholders}) ORDER BY name', ids):
                names.setdefault(row['shift_id'], []).append(row['name'])
        for row in rows:
            row['members'] = names.get(row['id'], [])
    return {'start': start.isoformat(), 'end': end.isoformat(), 'current': span['current'],
            'previous': (start - timedelta(days=7)).isoformat() if start.isoformat() > span['first'] else None,
            'next': (start + timedelta(days=7)).isoformat() if start.isoformat() < span['last'] else None,
            'days': [{'day': (start + timedelta(days=i)).isoformat(), 'weekday': i,
                      'shifts': [row for row in rows if row['day'] == (start + timedelta(days=i)).isoformat()]}
                     for i in range(7)]}
