"""Weekly roster drafts and explicit publication of dated duty shifts."""
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from server import db, schedule_store, system_clock

KEY = 'weekly_roster'


SEED = db.DATA / 'weekly_roster.json'


def load():
    """Saved roster from the database; falls back to the seed file in data/ on a fresh install."""
    with db.read() as conn:
        saved = db.get_setting(conn, KEY)
    if saved is not None:
        return saved
    # Data files live under data/, never beside the code, so they stay out of git.
    return empty_roster() if not SEED.exists() else json.loads(SEED.read_text(encoding='utf-8'))


def empty_roster():
    return {'location': '', 'needs_review': True,
            'slots': [{'label': f'第{i + 1}班', 'start': '', 'end': '', 'days': [[] for _ in range(7)]}
                      for i in range(4)]}


def validate(body, publishing=False):
    if not isinstance(body, dict):
        raise ValueError('请填写每周排班表')
    slots = body.get('slots')
    if not isinstance(slots, list) or len(slots) != 4:
        raise ValueError('每周排班需包含第 1～4 班')
    location = str(body.get('location') or '').strip()
    if len(location) > 120:
        raise ValueError('地点最多 120 字')
    normalized = []
    for i, slot in enumerate(slots):
        if not isinstance(slot, dict):
            raise ValueError('班次格式错误')
        start, end = str(slot.get('start') or ''), str(slot.get('end') or '')
        days = slot.get('days')
        if not isinstance(days, list) or len(days) != 7:
            raise ValueError('每班需包含周一到周日的人员')
        people = []
        for names in days:
            if not isinstance(names, list) or len(names) > 100 or any(
                not isinstance(n, str) or not n.strip() or len(n.strip()) > 40 for n in names
            ):
                raise ValueError('每格最多 100 位人员，姓名为 1～40 字')
            people.append(list(dict.fromkeys(n.strip() for n in names)))
        if publishing or start or end:
            # Use the same time validation as single-day editing.
            schedule_store.validate_shift(dict(day='2000-01-01', start=start, end=end,
                                               location=location or '待定', members=['校验']))
        normalized.append(dict(label=f'第{i + 1}班', start=start, end=end, days=people))
    if publishing:
        for previous, current in zip(normalized, normalized[1:]):
            _, end = schedule_store.shift_bounds(dict(day='2000-01-01', **previous))
            start, _ = schedule_store.shift_bounds(dict(day='2000-01-01', **current))
            if end > start:
                raise ValueError('班次时间应按第 1～4 班依次排列，且不能重叠')
        first, _ = schedule_store.shift_bounds(dict(day='2000-01-02', **normalized[0]))
        _, last = schedule_store.shift_bounds(dict(day='2000-01-01', **normalized[-1]))
        if last > first:
            raise ValueError('最后一班跨日结束时间与次日第一班重叠')
        if body.get('needs_review', True):
            raise ValueError('请先核对图片中的人员姓名并确认')
    return dict(location=location, slots=normalized, needs_review=bool(body.get('needs_review', True)))


def save(body):
    roster = validate(body)
    with db.write() as conn:
        db.set_setting(conn, KEY, roster)
    return roster


def publish_until(end_date, start_date=None):
    """Generate from `start_date` (default today) through `end_date`, inclusive."""
    today = system_clock.now_for(datetime).date()
    start = date.fromisoformat(start_date) if start_date else today
    end = date.fromisoformat(end_date)
    if start < today:
        raise ValueError('开始日期不能早于今天')
    if end < start:
        raise ValueError('结束日期不能早于开始日期')
    return _publish_range(start, end)


def publish(week_start):
    monday = date.fromisoformat(week_start)
    if monday.weekday() != 0:
        raise ValueError('请选择周一作为本周开始日期')
    return _publish_range(monday, monday + timedelta(days=6))


def _publish_range(start_day, end_day):
    # Read, validate, and publish in one transaction; other admins cannot interleave writes.
    with db.write() as conn:
        roster = validate(load(), publishing=True)
        schedule_store.init(conn)
        created = skipped = 0
        for offset in range((end_day - start_day).days + 1):
            current = start_day + timedelta(days=offset)
            weekday = current.weekday()
            day = current.isoformat()
            for index, slot in enumerate(roster['slots']):
                if not slot['days'][weekday]:
                    continue
                start, end = schedule_store.shift_bounds(dict(day=day, **slot))
                existing = conn.execute(
                    'SELECT shift_id FROM weekly_instances WHERE day=? AND slot=?',
                    (day, index)).fetchone()
                # A shift already generated for this slot is replaced, unless someone has
                # checked in: attendance must never be silently thrown away.
                replace_ids = set()
                if existing:
                    checked = conn.execute(
                        'SELECT 1 FROM attendance WHERE shift_id=? AND checked_at IS NOT NULL',
                        (existing['shift_id'],)).fetchone()
                    if checked:
                        skipped += 1
                        continue
                    replace_ids.add(existing['shift_id'])
                # Conflicts with other shifts are replaced too, when they hold no attendance.
                neighbors = conn.execute('SELECT * FROM shifts WHERE day>=? AND day<=?',
                    ((date.fromisoformat(day)-timedelta(days=1)).isoformat(),
                     (date.fromisoformat(day)+timedelta(days=1)).isoformat())).fetchall()
                conflict = False
                for other in neighbors:
                    if not (start < schedule_store.shift_bounds(other)[1] and
                            end > schedule_store.shift_bounds(other)[0]):
                        continue
                    checked = conn.execute(
                        'SELECT 1 FROM attendance WHERE shift_id=? AND checked_at IS NOT NULL',
                        (other['id'],)).fetchone()
                    if checked:
                        conflict = True
                        break
                    replace_ids.add(other['id'])
                if conflict:
                    skipped += 1
                    continue
                # A skipped slot must leave all existing shifts untouched.
                conn.executemany('DELETE FROM shifts WHERE id=?', [(sid,) for sid in sorted(replace_ids)])
                sid = conn.execute('INSERT INTO shifts(day,start,end,location,note) VALUES(?,?,?,?,?)',
                                   (day, slot['start'], slot['end'], roster['location'], slot['label'])).lastrowid
                conn.executemany('INSERT INTO attendance(shift_id,name) VALUES(?,?)',
                                 [(sid, name) for name in slot['days'][weekday]])
                conn.execute('INSERT INTO weekly_instances(day,slot,shift_id) VALUES(?,?,?)', (day, index, sid))
                created += 1
    return dict(created=created, skipped=skipped)
