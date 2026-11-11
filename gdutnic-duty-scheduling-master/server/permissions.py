"""Page access belongs to permission groups; each account belongs to one group."""
from __future__ import annotations

import hashlib
import uuid
from server import db, root_admin

PERMISSIONS = {
    'duty': ('打卡', '在概览中完成到岗签到'),
    'schedule_list': ('排班列表', '查看所有人员的已生成排班，不含考勤信息'),
    'member_field': ('外勤', '在概览中登记本人的外勤'),
    'field_confirm': ('外勤确认', '确认或退回待确认的外勤'),
    'overview': ('概览', '出勤统计与排班详情'),
    'attendance_edit': ('更改考勤状态', '在概览中把某人的考勤改判为准时、迟到或旷班'),
    'weekly': ('每周排班', '维护模板、生成和清除排班'),
    'special': ('特殊排班', '新增和管理临时班次'),
    'swap': ('调换值班', '创建和管理调班'),
    'permission_groups': ('权限组管理', '创建和管理权限组的访问范围'),
    'members': ('用户管理', '管理账号、密码与所属权限组'),
    'checkin_manage': ('打卡设置', '设置打卡和外勤的 IP 允许或排除规则'),
    'field': ('外勤管理', '查看和管理成员的外勤记录'),
    # `settings` is kept as the sidebar selector but is not a grantable leaf: the page
    # opens when the account holds any of its settings permissions.
    'settings': ('其他设置', '值班规则、打卡网络、SSO、系统时间与发件邮箱'),
    'rules': ('值班时间', '签到窗口与迟到/旷班阈值'),
    'clock': ('系统时间', '自动/手动系统时钟'),
    'smtp': ('SMTP', '配置发件邮箱服务器'),
    'sso': ('SSO 设置', '配置 GDUTNIC 单点登录与账号绑定'),
    'push': ('消息推送', '设置收件邮箱、推送周期和考勤邮件格式'),
    'logs': ('查看日志', '查看账号操作记录'),
    'data': ('数据管理', '查看和管理数据表'),
}
# Page selectors that are derived from child leaves rather than granted directly.
DERIVED = {'settings': ('rules', 'clock', 'smtp', 'checkin_manage', 'sso')}
GRANTABLE = tuple(key for key in PERMISSIONS if key not in DERIVED)
ALL_PERMISSIONS = tuple(PERMISSIONS)
DEFAULT_PERMISSIONS = ('duty', 'member_field', 'field_confirm')
DEFAULT_GROUP = 'member'
ADMIN_DEFAULTS = ALL_PERMISSIONS
MEMBER_PERMISSIONS = ('duty', 'schedule_list', 'member_field', 'field_confirm')
ADMIN_PERMISSIONS = frozenset(PERMISSIONS) - set(MEMBER_PERMISSIONS)
TREE = [
    {'id': 'member_area', 'label': '成员端', 'hint': '成员页面的侧栏入口', 'children': [
        {'id': 'member_overview_group', 'label': '概览', 'hint': '概览始终展示；分别控制打卡和外勤操作', 'children': [
            {'id': key, 'label': PERMISSIONS[key][0], 'hint': PERMISSIONS[key][1]}
            for key in ('duty', 'member_field')]},
        *[{'id': key, 'label': PERMISSIONS[key][0], 'hint': PERMISSIONS[key][1]}
          for key in ('schedule_list', 'field_confirm')]]},
    {'id': 'admin_area', 'label': '管理端', 'hint': '管理页面的侧栏入口', 'children': [
        {'id': 'overview_group', 'label': PERMISSIONS['overview'][0], 'hint': '出勤统计与排班详情', 'children': [
            {'id': 'overview', 'label': '查看概览', 'hint': PERMISSIONS['overview'][1]},
            {'id': 'attendance_edit', 'label': PERMISSIONS['attendance_edit'][0],
             'hint': PERMISSIONS['attendance_edit'][1]},
        ]},
        {'id': 'weekly', 'label': PERMISSIONS['weekly'][0], 'hint': PERMISSIONS['weekly'][1]},
        {'id': 'special', 'label': PERMISSIONS['special'][0], 'hint': PERMISSIONS['special'][1]},
        {'id': 'swap', 'label': PERMISSIONS['swap'][0], 'hint': PERMISSIONS['swap'][1]},
        {'id': 'field', 'label': PERMISSIONS['field'][0], 'hint': PERMISSIONS['field'][1]},
        {'id': 'members', 'label': PERMISSIONS['members'][0], 'hint': PERMISSIONS['members'][1]},
        {'id': 'permission_groups', 'label': PERMISSIONS['permission_groups'][0],
         'hint': PERMISSIONS['permission_groups'][1]},
        {'id': 'push', 'label': PERMISSIONS['push'][0], 'hint': PERMISSIONS['push'][1]},
        {'id': 'logs', 'label': PERMISSIONS['logs'][0], 'hint': PERMISSIONS['logs'][1]},
        {'id': 'data', 'label': PERMISSIONS['data'][0], 'hint': PERMISSIONS['data'][1]},
        {'id': 'settings_group', 'label': PERMISSIONS['settings'][0], 'hint': '值班规则、打卡网络、SSO、系统时间与发件邮箱', 'children': [
            {'id': 'rules', 'label': PERMISSIONS['rules'][0], 'hint': PERMISSIONS['rules'][1]},
            {'id': 'clock', 'label': PERMISSIONS['clock'][0], 'hint': PERMISSIONS['clock'][1]},
            {'id': 'smtp', 'label': PERMISSIONS['smtp'][0], 'hint': PERMISSIONS['smtp'][1]},
            {'id': 'checkin_manage', 'label': PERMISSIONS['checkin_manage'][0], 'hint': PERMISSIONS['checkin_manage'][1]},
            {'id': 'sso', 'label': PERMISSIONS['sso'][0], 'hint': PERMISSIONS['sso'][1]},
        ]},
    ]},
]
GROUP_IDS = ('member_area', 'member_overview_group', 'admin_area', 'overview_group', 'settings_group')
# Only used to upgrade stored permissions and console recovery calls from older builds.
LEGACY = {
    'user': DEFAULT_PERMISSIONS,
    'schedule': ('overview', 'attendance_edit', 'weekly', 'special', 'swap',
                 'rules', 'clock', 'smtp', 'logs', 'data'),
    # Releases before the split granted the page as a single leaf; keep both cards.
    'settings': ('rules', 'clock', 'smtp'),
    'accounts': ('members',), 'accounts_manage': ('members',),
    'accounts_protected': ('members', 'permission_groups'),
}


def leaves(nodes=None):
    result = []
    for node in TREE if nodes is None else nodes:
        result.extend(leaves(node['children']) if 'children' in node else [node['id']])
    return result


def _clean(values):
    if not isinstance(values, (list, tuple, set)):
        return set()
    # `settings` is a derived selector; only its individual permissions are stored.
    return {v for v in values if isinstance(v, str) and v in PERMISSIONS and v not in DERIVED}


def _upgrade(values):
    result = set()
    for value in values:
        result.update(LEGACY.get(value, (value,)))
    return _clean(result)


def encode(values):
    return ','.join(sorted(_clean(values)))


def decode(stored):
    return _clean(str(stored or '').split(','))


def _group_for_permissions(conn, values):
    """Preserve a legacy account's access in a reusable group, never silently elevate it."""
    values = _clean(values)
    stored = encode(values)
    # Reuse only an exact match: an operator may have edited a migrated group since boot.
    if values == set(DEFAULT_PERMISSIONS):
        default = conn.execute('SELECT permissions FROM permission_groups WHERE id=?', (DEFAULT_GROUP,)).fetchone()
        if default and default['permissions'] == stored:
            return DEFAULT_GROUP
    matching = conn.execute('SELECT id FROM permission_groups WHERE permissions=? ORDER BY id LIMIT 1', (stored,)).fetchone()
    if matching:
        return matching['id']
    group_id = 'legacy_' + hashlib.sha256(stored.encode()).hexdigest()[:20]
    if conn.execute('SELECT id FROM permission_groups WHERE id=?', (group_id,)).fetchone():
        group_id = uuid.uuid4().hex
    label = '管理员' if values == set(ALL_PERMISSIONS) else '原有权限组 · ' + group_id[-6:]
    if conn.execute('SELECT id FROM permission_groups WHERE name=?', (label,)).fetchone():
        label += ' · ' + group_id[-6:]
    conn.execute('INSERT INTO permission_groups(id,name,permissions) VALUES(?,?,?)',
                 (group_id, label, stored))
    return group_id


def _ensure_groups(conn):
    db.insert_missing(conn, 'permission_groups',
                      {'id': DEFAULT_GROUP, 'name': '会员', 'permissions': encode(DEFAULT_PERMISSIONS)}, ['id'])
    rows = conn.execute("SELECT username,permissions FROM member_accounts WHERE group_id='' OR group_id IS NULL").fetchall()
    for row in rows:
        values = _upgrade(str(row['permissions'] or '').split(',')) if row['permissions'] else set(DEFAULT_PERMISSIONS)
        group = _group_for_permissions(conn, values)
        conn.execute('UPDATE member_accounts SET group_id=? WHERE username=?', (group, row['username']))


def ensure_defaults():
    with db.write() as conn:
        _ensure_groups(conn)
        if not db.get_setting(conn, 'setup_done', '') and root_admin.exists():
            db.set_setting(conn, 'setup_done', root_admin.username())
        root_admin.purge_mirror_row(conn)


def _derive(values):
    """Add the page selectors implied by the granted leaves."""
    result = set(values)
    for page, children in DERIVED.items():
        if result & set(children):
            result.add(page)
    return result


def permissions_of(name):
    if root_admin.is_root(name):
        return set(ALL_PERMISSIONS)
    with db.read() as conn:
        row = conn.execute('SELECT m.active,m.group_id,m.permissions AS legacy,g.permissions '
                           'FROM member_accounts m LEFT JOIN permission_groups g ON g.id=m.group_id '
                           'WHERE m.username=?', (name,)).fetchone()
    if not row or not row['active']:
        return set()
    if row['group_id']:
        return _derive(decode(row['permissions']))  # Missing groups fail closed.
    stored = _upgrade(str(row['legacy']).split(',')) if row['legacy'] else set(DEFAULT_PERMISSIONS)
    return _derive(stored)


def has(name, permission):
    held = permissions_of(name)
    return bool(held & set(LEGACY[permission])) if permission in LEGACY else permission in held


def is_admin(name):
    return bool(permissions_of(name) & ADMIN_PERMISSIONS)


def can_admin(name):
    return is_admin(name)


def drop_cache(name):
    from server.security import MEMBER_SESSIONS
    MEMBER_SESSIONS.clear(name + ':')


def list_groups():
    ensure_defaults()
    with db.read() as conn:
        rows = conn.execute('SELECT g.id,g.name,g.permissions,COUNT(m.username) AS member_count '
                            'FROM permission_groups g LEFT JOIN member_accounts m ON m.group_id=g.id '
                            'GROUP BY g.id,g.name,g.permissions ORDER BY g.name').fetchall()
    items = [dict(id=r['id'], name=r['name'], permissions=sorted(decode(r['permissions'])),
                  member_count=r['member_count'], default=r['id'] == DEFAULT_GROUP) for r in rows]
    return sorted(items, key=lambda item: (not item['default'], item['name']))


def _validate(name, values):
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 64:
        raise ValueError('权限组名称应为 1～64 个字符')
    if not isinstance(values, list) or any(
            not isinstance(v, str) or v not in PERMISSIONS or v in DERIVED for v in values):
        raise ValueError('请选择有效的页面权限')
    return name.strip(), encode(values)


def save_group(name, values, group_id=None):
    name, stored = _validate(name, values)
    with db.write() as conn:
        _ensure_groups(conn)
        if group_id and not conn.execute('SELECT id FROM permission_groups WHERE id=?', (group_id,)).fetchone():
            raise LookupError('权限组不存在')
        if group_id == DEFAULT_GROUP and name != '会员':
            raise ValueError('默认权限组名称固定为“会员”')
        if conn.execute('SELECT id FROM permission_groups WHERE name=? AND id<>?', (name, group_id or '')).fetchone():
            raise ValueError('权限组名称已存在')
        if group_id:
            conn.execute('UPDATE permission_groups SET name=?,permissions=? WHERE id=?', (name, stored, group_id))
        else:
            group_id = uuid.uuid4().hex
            conn.execute('INSERT INTO permission_groups(id,name,permissions) VALUES(?,?,?)', (group_id, name, stored))
    return {'id': group_id, 'name': name, 'permissions': sorted(decode(stored))}


def delete_group(group_id):
    if group_id == DEFAULT_GROUP:
        raise ValueError('默认权限组“会员”不能删除')
    with db.write() as conn:
        _ensure_groups(conn)
        if not conn.execute('SELECT id FROM permission_groups WHERE id=?', (group_id,)).fetchone():
            raise LookupError('权限组不存在')
        count = conn.execute('UPDATE member_accounts SET group_id=? WHERE group_id=?',
                             (DEFAULT_GROUP, group_id)).rowcount
        conn.execute('DELETE FROM permission_groups WHERE id=?', (group_id,))
    return {'moved': count}


def set_group(name, group_id):
    if root_admin.is_root(name):
        raise ValueError('原始管理员的权限不可更改')
    if not isinstance(group_id, str) or not group_id:
        raise ValueError('请选择权限组')
    with db.write() as conn:
        _ensure_groups(conn)
        if not conn.execute('SELECT id FROM permission_groups WHERE id=?', (group_id,)).fetchone():
            raise LookupError('权限组不存在')
        if not conn.execute('SELECT username FROM member_accounts WHERE username=?', (name,)).fetchone():
            raise LookupError('账号不存在')
        conn.execute('UPDATE member_accounts SET group_id=? WHERE username=?', (group_id, name))
    drop_cache(name)
    return permissions_of(name)


def set_permissions(name, values):
    """Console/legacy helper: assign a group. HTTP never accepts individual user options."""
    if root_admin.is_root(name):
        raise ValueError('原始管理员的权限不可更改')
    with db.write() as conn:
        _ensure_groups(conn)
        group = _group_for_permissions(conn, _upgrade(values))
        if not conn.execute('UPDATE member_accounts SET group_id=?,permissions=? WHERE username=?',
                            (group, encode(_upgrade(values)), name)).rowcount:
            raise LookupError('账号不存在')
    drop_cache(name)
    return permissions_of(name)
