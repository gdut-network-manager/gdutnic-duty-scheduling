"""Sunday email digests for complete Sunday–Saturday attendance periods."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime, time, timedelta
import hashlib
from html import escape
import re
from zoneinfo import ZoneInfo

from pypinyin import lazy_pinyin
from server import admin_store, db, duty_settings, mailer, overview, schedule_store

TZ = ZoneInfo('Asia/Shanghai')
KEY = 'attendance_push'
COLUMNS = {'name': '名称', 'count': '次数', 'dates': '日期时间', 'month_late': '本月累计迟到次数', 'group': '分组'}
LABELS = {'late': '迟到', 'absent': '旷班'}
DEFAULTS = {
    'enabled': False, 'recipients': [], 'weeks': 1, 'send_time': '09:00',
    'title': '考勤情况 · {开始日期} 至 {结束日期}',
    'opening': '以下为 {开始日期} 至 {结束日期} 的考勤情况。',
    'closing': '请核对以上记录，如有疑问请联系管理员。',
    'sections': {kind: {'enabled': True, 'columns': [
        {'key': key, 'enabled': key in ('name', 'count', 'dates')} for key in COLUMNS],
        'sort': {'key': 'count', 'direction': 'desc'}} for kind in LABELS},
}


def real_now():
    return datetime.now(TZ)


def load():
    with db.read() as conn:
        saved = db.get_setting(conn, KEY, {}) or {}
    config = deepcopy(DEFAULTS)
    config.update(saved)
    return config


def validate(body, ready=False):
    if not isinstance(body, dict):
        raise ValueError('推送配置格式不正确')
    config = deepcopy(DEFAULTS)
    config['enabled'] = body.get('enabled') is True
    recipients = body.get('recipients', [])
    if not isinstance(recipients, list) or len(recipients) > 50:
        raise ValueError('最多添加 50 个目标邮箱')
    config['recipients'] = []
    for address in recipients:
        if not isinstance(address, str) or not admin_store._looks_like_address(address.strip()) or len(address) > 254 or not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', address.strip()):
            raise ValueError('请填写有效的目标邮箱')
        address = address.strip()
        if address.lower() not in {value.lower() for value in config['recipients']}:
            config['recipients'].append(address)
    if type(body.get('weeks', 1)) is not int or body.get('weeks', 1) not in (1, 2, 4):
        raise ValueError('推送频率只能选择 1、2、4 周一次')
    config['weeks'] = body.get('weeks', 1)
    send_time = body.get('send_time', '09:00')
    if not isinstance(send_time, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', send_time):
        raise ValueError('请选择有效的周日发件时间')
    config['send_time'] = send_time
    for key, limit in (('title', 200), ('opening', 5000), ('closing', 5000)):
        value = body.get(key, DEFAULTS[key])
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError(f'{key} 格式不正确或内容过长')
        if key == 'title' and (not value.strip() or '\r' in value or '\n' in value):
            raise ValueError('标题不能为空，也不能包含换行')
        config[key] = value
    sections = body.get('sections', DEFAULTS['sections'])
    if not isinstance(sections, dict):
        raise ValueError('请选择要推送的考勤项目')
    for kind in LABELS:
        section = sections.get(kind)
        if not isinstance(section, dict) or not isinstance(section.get('columns'), list):
            raise ValueError('列配置不正确')
        columns = section['columns']
        if len(columns) != len(COLUMNS) or any(not isinstance(c, dict) or not isinstance(c.get('key'), str) for c in columns):
            raise ValueError('列配置不完整')
        if {c['key'] for c in columns} != set(COLUMNS):
            raise ValueError('列配置包含未知或重复的项目')
        columns = [{'key': c['key'], 'enabled': c.get('enabled') is True} for c in columns]
        # Disabled columns always follow enabled columns, preserving relative order.
        columns.sort(key=lambda c: not c['enabled'])
        sort = section.get('sort', {})
        if not isinstance(sort, dict) or not isinstance(sort.get('key'), str) or sort.get('key') not in COLUMNS or sort.get('direction') not in ('asc', 'desc'):
            raise ValueError('排序配置不正确')
        if section.get('enabled') and not any(c['enabled'] for c in columns):
            raise ValueError(f'{LABELS[kind]}至少需要启用一列')
        config['sections'][kind] = {'enabled': section.get('enabled') is True, 'columns': columns, 'sort': dict(sort)}
    if ready and config['enabled']:
        if not config['recipients']:
            raise ValueError('启用前请添加目标邮箱')
        if not any(s['enabled'] for s in config['sections'].values()):
            raise ValueError('至少选择迟到或旷班中的一项')
        smtp = admin_store.smtp_config()
        if not smtp.get('enabled') or not smtp.get('host') or not smtp.get('from_address'):
            raise ValueError('请先在「其他设置 → SMTP」配置并启用发件邮箱')
    return config


def next_sunday(now, send_time):
    hour, minute = map(int, send_time.split(':'))
    day = now.date() + timedelta(days=(6 - now.weekday()) % 7)
    result = datetime.combine(day, time(hour, minute), TZ)
    return result if result > now else result + timedelta(days=7)


def save(body, now=None):
    now = now or real_now()
    config = validate(body, ready=True)
    with db.write() as conn:
        previous = db.get_setting(conn, KEY, {}) or {}
        same_schedule = (previous.get('enabled') and config['enabled']
                         and previous.get('weeks') == config['weeks'] and previous.get('send_time') == config['send_time'])
        config['next_run'] = previous.get('next_run') if same_schedule else None
        if config['enabled'] and not config['next_run']:
            config['next_run'] = next_sunday(now, config['send_time']).isoformat()
        db.set_setting(conn, KEY, config)
    return config


def period(weeks, end=None, now=None):
    now = now or real_now()
    if end is None:
        # Last fully completed Saturday. A Saturday being lived is not complete yet.
        end = now.date() - timedelta(days=(now.weekday() + 1) % 7 + 1)
    if isinstance(end, str):
        end = date.fromisoformat(end)
    if end.weekday() != 5:
        raise ValueError('统计周期必须在周六结束')
    start = end - timedelta(days=7 * weeks - 1)
    cutoff = datetime.combine(end + timedelta(days=1), time.min, TZ) - timedelta(microseconds=1)
    return start, end, cutoff


def _events(start, end, cutoff):
    rules = duty_settings.load()
    events, seen = [], set()
    with db.read() as conn:
        schedule_store.init(conn)
        shifts = conn.execute('SELECT * FROM shifts WHERE day>=? AND day<=? ORDER BY day,start,id',
                              (start.isoformat(), end.isoformat())).fetchall()
        attendance = overview._attendance_by_shift(conn, shifts)
        for shift in shifts:
            if schedule_store.shift_bounds(shift)[0] > cutoff:
                continue
            for member in attendance.get(shift['id'], []):
                checked = member.get('checked_at')
                if checked:
                    at = datetime.fromisoformat(checked)
                    if at.tzinfo is None: at = at.replace(tzinfo=TZ)
                    if at > cutoff: member = {**member, 'checked_at': None}
                seen.add((shift['day'], shift['start'], member['name'], checked))
                status = overview.member_status(shift, member, cutoff, rules)
                if status in LABELS:
                    events.append({'name': member['name'], 'kind': status, 'day': shift['day'],
                                   'at': shift['day'] + ' ' + shift['start']})
        for record in conn.execute('SELECT day,start,end,name,checked_at FROM attendance_history WHERE day>=? AND day<=?',
                                   (start.isoformat(), end.isoformat())):
            record = dict(record)
            if (record['day'], record['start'], record['name'], record['checked_at']) in seen:
                continue
            if schedule_store.shift_bounds(record)[0] > cutoff:
                continue
            checked = datetime.fromisoformat(record['checked_at'])
            if checked.tzinfo is None: checked = checked.replace(tzinfo=TZ)
            status = overview.member_status(record, {'checked_at': record['checked_at'] if checked <= cutoff else None}, cutoff, rules)
            if status in LABELS:
                events.append({'name': record['name'], 'kind': status, 'day': record['day'],
                               'at': record['day'] + ' ' + record['start']})
        groups = {row['username']: row['group_name'] or '未分组' for row in conn.execute(
            'SELECT m.username,g.name AS group_name FROM member_accounts m '
            'LEFT JOIN permission_groups g ON g.id=m.group_id')}
    return events, groups


def report(config, end=None, now=None, sample=False):
    start, end, cutoff = period(config['weeks'], end, now)
    month_first = end.replace(day=1)
    events, groups = _events(min(start, month_first), end, cutoff)
    month_late = {}
    for event in events:
        if event['kind'] == 'late' and event['day'] >= month_first.isoformat():
            month_late[event['name']] = month_late.get(event['name'], 0) + 1
    aggregate = {kind: {} for kind in LABELS}
    for event in events:
        if event['day'] < start.isoformat(): continue
        rows = aggregate[event['kind']]
        row = rows.setdefault(event['name'], {'name': event['name'], 'count': 0, 'dates': [],
                    'month_late': month_late.get(event['name'], 0), 'group': groups.get(event['name'], '未分组')})
        row['count'] += 1; row['dates'].append(event['at'])
    if sample:
        aggregate = {kind: {
            '示例成员甲': {'name': '示例成员甲', 'count': 2, 'dates': [f'{start} 09:00', f'{start + timedelta(days=2)} 14:00'], 'month_late': 3, 'group': '会员'},
            '示例成员乙': {'name': '示例成员乙', 'count': 1, 'dates': [f'{end} 09:00'], 'month_late': 1, 'group': '外勤组'},
        } for kind in LABELS}
    sections = {}
    for kind, data in aggregate.items():
        section = config['sections'][kind]
        rows = list(data.values())
        for row in rows: row['dates'].sort()
        rows.sort(key=lambda row: ''.join(lazy_pinyin(row['name'])))
        key = section['sort']['key']
        def sort_value(row):
            if key == 'dates': return row['dates'][0] if row['dates'] else ''
            if key in ('name', 'group'): return ''.join(lazy_pinyin(row[key]))
            return row[key]
        rows.sort(key=sort_value, reverse=section['sort']['direction'] == 'desc')
        columns = [c['key'] for c in section['columns'] if c['enabled']]
        cells = []
        for row in rows:
            for index in range(len(row['dates']) if 'dates' in columns else 1):
                cells.append([row['dates'][index] if key == 'dates' else row[key] if index == 0 else '' for key in columns])
        sections[kind] = {'enabled': section['enabled'], 'columns': columns, 'rows': cells, 'people': len(rows)}
    tokens = {'{开始日期}': str(start), '{结束日期}': str(end), '{周期周数}': str(config['weeks'])}
    def text(key):
        value = config[key]
        for token, replacement in tokens.items(): value = value.replace(token, replacement)
        return value
    return {'start': str(start), 'end': str(end), 'cutoff': cutoff.isoformat(), 'sample': sample,
            'title': text('title'), 'opening': text('opening'), 'closing': text('closing'),
            'sections': sections, 'labels': COLUMNS}


def render_email(data):
    plain = [data['opening'], '']
    content = [f'<p style="white-space:pre-wrap">{escape(data["opening"])}</p>']
    for kind, section in data['sections'].items():
        if not section['enabled']: continue
        plain.append(LABELS[kind])
        content.append(f'<h2 style="font-size:18px;color:#186078">{LABELS[kind]}</h2>')
        if not section['rows']:
            message = f'本周期无{LABELS[kind]}记录'
            plain.extend([message, '']); content.append('<p>' + message + '</p>'); continue
        plain.append('\t'.join(COLUMNS[key] for key in section['columns']))
        content.append('<table style="width:100%;border-collapse:collapse;text-align:left"><thead><tr>')
        for key in section['columns']:
            content.append(f'<th style="padding:10px;background:#d0e6f2">{COLUMNS[key]}</th>')
        content.append('</tr></thead><tbody>')
        for cells in section['rows']:
            plain.append('\t'.join(str(cell) for cell in cells))
            content.append('<tr>' + ''.join(f'<td style="padding:8px 10px;border-bottom:1px solid #eef3f7;vertical-align:top">{escape(str(cell))}</td>' for cell in cells) + '</tr>')
        plain.append(''); content.append('</tbody></table>')
    plain.append(data['closing'])
    content.append(f'<p style="white-space:pre-wrap">{escape(data["closing"])}</p>')
    html = ('<!doctype html><html lang="zh-CN"><body style="font-family:Arial,sans-serif;color:#191c1e;background:#f6f9fc;padding:20px">'
            '<main style="max-width:900px;margin:auto;background:white;padding:24px;border-radius:20px">'
            f'<h1 style="font-size:22px">{escape(data["title"])}</h1>' + ''.join(content) + '</main></body></html>')
    return '\n'.join(plain), html


def _delivery_id(end, recipient):
    return hashlib.sha256((end + ':' + recipient.lower()).encode()).hexdigest()


def run_due(now=None):
    """Claim each recipient durably, avoiding duplicate sends across server workers."""
    now = now or real_now()
    config = load()
    if not config['enabled'] or not config.get('next_run'): return
    due = datetime.fromisoformat(config['next_run'])
    if due > now: return
    interval = timedelta(weeks=config['weeks'])
    due += interval * int((now - due) // interval)  # After downtime, send the latest due period.
    end = (due.date() - timedelta(days=1)).isoformat()
    data = report(config, end=end)
    plain, html = render_email(data)
    for recipient in config['recipients']:
        delivery_id = _delivery_id(end, recipient)
        with db.write() as conn:
            current = db.get_setting(conn, KEY, {}) or {}
            if not current.get('enabled') or recipient not in current.get('recipients', []):
                continue
            row = conn.execute('SELECT state,updated_at FROM push_deliveries WHERE id=?', (delivery_id,)).fetchone()
            if row and (row['state'] != 'failed' or now - datetime.fromisoformat(row['updated_at']) < timedelta(minutes=15)):
                continue
            if row:
                conn.execute("UPDATE push_deliveries SET state='sending',updated_at=?,error='' WHERE id=?", (now.isoformat(), delivery_id))
            else:
                conn.execute('INSERT INTO push_deliveries(id,period_end,recipient,state,updated_at,error) VALUES(?,?,?,?,?,?)',
                             (delivery_id, end, recipient, 'sending', now.isoformat(), ''))
        try:
            mailer.send(recipient, data['title'], plain, html=html)
        except Exception as exc:
            with db.write() as conn:
                conn.execute("UPDATE push_deliveries SET state='failed',updated_at=?,error=? WHERE id=?",
                             (now.isoformat(), str(exc)[:1000], delivery_id))
        else:
            with db.write() as conn:
                conn.execute("UPDATE push_deliveries SET state='sent',updated_at=?,error='' WHERE id=?", (now.isoformat(), delivery_id))
    with db.write() as conn:
        states = {row['id']: row['state'] for row in conn.execute('SELECT id,state FROM push_deliveries WHERE period_end=?', (end,))}
        if config['recipients'] and all(states.get(_delivery_id(end, recipient)) == 'sent' for recipient in config['recipients']):
            latest = db.get_setting(conn, KEY, {}) or {}
            # A simultaneous edit or disable must not be overwritten by a slow SMTP send.
            if latest.get('next_run') == config['next_run'] and latest.get('weeks') == config['weeks']:
                latest['next_run'] = (due + interval).isoformat()
                db.set_setting(conn, KEY, latest)


def status():
    config = load()
    with db.read() as conn:
        rows = [dict(row) for row in conn.execute('SELECT period_end,recipient,state,updated_at,error FROM push_deliveries ORDER BY updated_at DESC LIMIT 20')]
    return {'config': config, 'deliveries': rows, 'smtp_ready': all(admin_store.smtp_config().get(key) for key in ('enabled', 'host', 'from_address'))}


async def worker():
    while True:
        try:
            await asyncio.to_thread(run_due)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Report errors in persisted delivery state where possible; keep the worker alive.
            __import__('logging').getLogger(__name__).exception('考勤推送任务执行失败')
        await asyncio.sleep(30)
