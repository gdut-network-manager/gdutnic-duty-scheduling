"""Read-only-ish database console for administrators.

Only whitelisted tables and columns are reachable. Table and column names always come
from the constants below, never from the request, so no SQL identifier is ever
interpolated from user input. Values are bound parameters.

Secrets (password hashes, API keys) are masked in previews and cannot be written
through this console: password changes go through the account page so the hash stays
valid, and connector settings go through 其他设置.
"""
from __future__ import annotations

from server import db, field_duty

# label, primary key, editable columns, searchable columns, masked columns
TABLES = {
    'shifts': {
        'label': '班次', 'pk': ['id'],
        'editable': ['day', 'start', 'end', 'location', 'note', 'special'],
        'search': ['day', 'start', 'end', 'location', 'note'],
        'masked': [],
    },
    'attendance': {
        'label': '考勤人员', 'pk': ['shift_id', 'name'],
        'editable': ['checked_at', 'status_override'],
        'search': ['name', 'checked_at', 'status_override'],
        'masked': [],
        # The shift's own date and times are the meaningful timestamp for this row;
        # `checked_at` is the sign-in moment and may be empty.
        'joins': [{'table': 'shifts', 'alias': 's', 'on': [('shift_id', 'id')],
                   'columns': ['day', 'start', 'end'], 'prefix': '班次'}],
    },
    'attendance_history': {
        'label': '出勤归档', 'pk': ['id'],
        'editable': [],  # archives are immutable; delete is still allowed
        'search': ['day', 'start', 'end', 'name'],
        'masked': [],
    },
    'field_duty': {
        'label': '外勤记录', 'pk': ['month', 'username'],
        'editable': ['registered_at', 'manual', 'status'],
        'search': ['month', 'username', 'registered_at'],
        'masked': [],
    },
    'swaps': {
        'label': '调班', 'pk': ['id'],
        'editable': ['shift_a', 'name_a', 'shift_b', 'name_b', 'created_at'],
        'search': ['name_a', 'name_b', 'created_at'],
        'masked': [],
    },
    'weekly_instances': {
        'label': '每周生成记录', 'pk': ['day', 'slot'],
        'editable': ['shift_id'],
        'search': ['day'],
        'masked': [],
    },
    'member_accounts': {
        'label': '账号', 'pk': ['username'],
        # The 原始管理员 lives in data/root_admin.json, so nothing here can touch it.
        'editable': ['active'],
        'search': ['username', 'group_id'],
        'masked': ['password'],
        'exclude_column': 'username',
    },
    'operation_log': {
        'label': '操作日志', 'pk': ['id'],
        'editable': [],  # the audit trail is append-only
        'deletable': False,  # and never removable, not even with the data permission
        'search': ['actor', 'action', 'detail'],
        'masked': [],
    },
    'settings': {
        'label': '站点设置', 'pk': ['key'],
        # Writing a settings row is restricted further in update_row: only the keys that
        # the data console is allowed to expose there can ever change.
        'editable': ['value'],
        'search': ['key'],
        'masked': [],  # masked per row below, since only some keys are secret
        # Deleting a setting can strand the site (bootstrap flag, admin path, session
        # secret), so rows here are read/write but never removable from this console.
        'deletable': False,
    },
    'users': {
        # Legacy console administrators, already folded into the single 原始管理员.
        # This table is a read-only historical record: no editing and no deleting.
        'label': '原始管理员（旧版记录）', 'pk': ['username'],
        'editable': [],
        'deletable': False,
        'search': ['username'],
        'masked': ['password'],
    },
    'visitors': {
        'label': '访客', 'pk': ['id'],
        'editable': ['current_id'],
        'search': ['id'],
        'masked': [],
    },
    'chats': {
        'label': '旧版会话', 'pk': ['visitor_id', 'id'],
        'editable': ['title'],
        'search': ['title'],
        'masked': [],
    },
    'messages': {
        'label': '旧版消息', 'pk': ['visitor_id', 'chat_id', 'seq'],
        'editable': [],
        'search': ['role'],
        'masked': [],
    },
    'oauth_clients': {
        'label': 'OAuth 客户端', 'pk': ['origin', 'redirect_uri'],
        'editable': [],
        'search': ['origin'],
        'masked': ['client_secret'],
    },
}
# Settings keys whose values are credentials, not readable configuration.
SECRET_SETTING_PARTS = ('mcp', 'qq', 'oauth', 'password', 'secret', 'key', 'model', 'smtp', 'sso', 'keycloak')
# Settings rows this console may write. Everything else (the bootstrap flag, the admin
# path, the signing secret, rules, clocks, sessions, SSO) is only reachable through its
# own screen, which also checks the matching permission. Without this list the data
# permission alone could rewrite, for example, setup_done and re-open registration.
WRITABLE_SETTINGS = frozenset({'chat_max_mb'})

PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


def _table(name):
    meta = TABLES.get(str(name))
    if not meta:
        raise LookupError('未知的数据表')
    return meta


def _quote(name):
    """Identifiers come from the whitelist above, but quoting still avoids reserved words."""
    return '`' + str(name).replace('`', '') + '`'


def columns(conn, table):
    """Actual columns of `table`, with their declared types."""
    if isinstance(conn, db.MysqlConnection):
        rows = conn.execute(
            'SELECT COLUMN_NAME AS name, DATA_TYPE AS type FROM information_schema.COLUMNS'
            ' WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=? ORDER BY ORDINAL_POSITION',
            (table,)).fetchall()
        return [(row['name'], str(row['type'] or '')) for row in rows]
    return [(row['name'], str(row['type'] or ''))
            for row in conn.execute(f'PRAGMA table_info({_quote(table)})').fetchall()]


def _is_secret(table, key, value):
    if 'password' in (TABLES[table]['masked'] or []):
        if table in ('member_accounts', 'users'):
            return True
    if table == 'settings' and any(part in str(key).lower() for part in SECRET_SETTING_PARTS):
        return True
    return False


def _mask(table, row, masked_columns):
    out = dict(row)
    for column in masked_columns:
        if out.get(column) not in (None, ''):
            out[column] = '••••••'
    if table == 'settings' and _is_secret(table, out.get('key'), out.get('value')):
        out['value'] = '••••••'
    return out


def list_tables():
    """Table labels with live row counts, newest data first."""
    result = []
    with db.read() as conn:
        # Some tables are created lazily by their own module.
        field_duty.init(conn)
        for name, meta in TABLES.items():
            try:
                count = conn.execute(f'SELECT COUNT(*) AS n FROM {_quote(name)}').fetchone()['n']
            except Exception:
                continue  # the table may not exist in an old database
            result.append({'name': name, 'label': meta['label'], 'rows': count,
                           'editable': bool(meta['editable']),
                           'deletable': meta.get('deletable', True)})
    return {'items': result}


def _excluded(name, meta):
    """The built-in root account must not appear in the data console either."""
    if not meta.get('exclude_column') or name != 'member_accounts':
        return None
    from server import root_admin
    if not root_admin.exists():
        return None
    return (meta['exclude_column'], root_admin.username())


def _join_clause(meta, present):
    """INNER JOINs declared on a table, so related timestamps can be shown with a row."""
    parts = []
    for combo in meta.get('joins') or []:
        conditions = []
        for left, right in combo['on']:
            if left not in present:
                return ''  # an unreadable schema simply loses the extra columns
            conditions.append(f't.{_quote(left)}={combo["alias"]}.{_quote(right)}')
        parts.append(f'INNER JOIN {_quote(combo["table"])} {combo["alias"]}'
                     f' ON ' + ' AND '.join(conditions))
    return ' '.join(parts)


def _joined_columns(meta, present):
    """Read-only display columns borrowed from a related table."""
    out = []
    for combo in meta.get('joins') or []:
        for column in combo['columns']:
            label = f'{combo["prefix"]}.{column}'
            out.append({'name': label,
                        'sql': f'{combo["alias"]}.{_quote(column)} AS {_quote(label)}',
                        'editable': False, 'secret': False, 'type': ''})
    return out


def _filter(name, meta, present, q):
    """WHERE fragment and bound parameters shared by preview and bulk deletes."""
    params, clauses = [], []
    excluded = _excluded(name, meta)
    if excluded and excluded[0] in present:
        clauses.append(f't.{_quote(excluded[0])}<>?')
        params.append(excluded[1])
    term = str(q or '').strip()
    if term:
        searchable = [column for column in meta['search'] if column in present]
        if searchable:
            clauses.append('(' + ' OR '.join(f't.{_quote(column)} LIKE ?' for column in searchable) + ')')
            params.extend([f'%{term}%'] * len(searchable))
    return ((' WHERE ' + ' AND '.join(clauses)) if clauses else ''), params


def preview(name, page=1, size=PAGE_SIZE, q='', actor=''):
    """One page of rows, with secrets masked and column metadata for the editor."""
    meta = _table(name)
    page = max(1, int(page or 1))
    size = max(1, min(int(size or PAGE_SIZE), MAX_PAGE_SIZE))
    with db.read() as conn:
        actual = columns(conn, name)
        present = [column for column, _type in actual]
        if not present:
            raise LookupError('数据表结构不可用')
        where, params = _filter(name, meta, present, q)
        # Always alias the table so filters and ordering read the same either way.
        source = f'{_quote(name)} t'
        joined = _join_clause(meta, present)
        if joined:
            source += ' ' + joined
        total = conn.execute(f'SELECT COUNT(*) AS n FROM {source}{where}', params).fetchone()['n']
        order = ', '.join('t.' + _quote(column) for column in meta['pk'] if column in present)
        selects = ['t.*'] + ([column['sql'] for column in _joined_columns(meta, present)] if joined else [])
        rows = conn.execute(
            f'SELECT {", ".join(selects)} FROM {source}{where}'
            + (f' ORDER BY {order}' if order else '')
            + ' LIMIT ? OFFSET ?',
            [*params, size, (page - 1) * size]).fetchall()
    masked_columns = [column for column in meta['masked'] if column in present]
    columns_meta = [{'name': column, 'type': _type,
                     'editable': column in meta['editable'],
                     'secret': column in masked_columns}
                    for column, _type in actual]
    columns_meta.extend(_joined_columns(meta, present))
    items = [_mask(name, dict(row), masked_columns) for row in rows]
    if meta.get('deletable', True):
        from server import root_admin
        for item in items:
            if name == 'member_accounts' and (root_admin.is_root(item.get('username'))
                                              or _privileged({'username': item.get('username')}, actor)):
                item['deletable'] = False
            else:
                item['deletable'] = True
    else:
        for item in items:
            item['deletable'] = False
    return {
        'name': name, 'label': meta['label'], 'page': page, 'size': size,
        'total': total, 'pages': max(1, (total + size - 1) // size),
        'pk': [column for column in meta['pk'] if column in present],
        'deletable': meta.get('deletable', True),
        'columns': columns_meta,
        'items': items,
    }


def _coerce(conn, table, column, value):
    """Store integers as integers and everything else as text, matching the column."""
    types = dict(columns(conn, table))
    declared = types.get(column, '').lower()
    if value is None or value == '':
        if 'int' in declared or declared in ('tinyint', 'smallint', 'bigint', 'integer'):
            return 0
        return ''
    if 'int' in declared:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f'{column} 需要填写整数') from exc
    return str(value)


def _key_clause(meta, present, keys):
    """WHERE clause from the primary key plus its bound values."""
    clauses, values = [], []
    for column in meta['pk']:
        if column not in present:
            raise LookupError('主键列不存在，无法定位这一行')
        if column not in keys:
            raise ValueError(f'缺少主键 {column}')
        clauses.append(f'{_quote(column)}=?')
        values.append(keys[column])
    return ' AND '.join(clauses), values


def _reject_root(name, keys):
    if name != 'member_accounts':
        return
    from server import root_admin
    if root_admin.is_root(keys.get('username')):
        raise ValueError('原始管理员不在此表中管理')


def _privileged(keys, actor=''):
    """True when this row is a privileged account the actor may not touch from here.

    The check must not go through `permissions.is_admin`: that returns False for a
    disabled account, which would let a data-only operator re-enable a disabled
    administrator (or delete the row outright) without the account permission.
    """
    from server import permissions
    target = str((keys or {}).get('username') or '')
    if not target or target == (actor or ''):
        return False
    if permissions.has(actor, 'members') and permissions.has(actor, 'permission_groups'):
        return False
    return _account_is_admin(target)


def _account_is_admin(name):
    """Whether the stored row is (or was) an administrator, ignoring `active`."""
    from server import permissions, root_admin
    if root_admin.is_root(name):
        return True
    with db.read() as conn:
        row = conn.execute('SELECT group_id,permissions AS legacy FROM member_accounts WHERE username=?',
                           (name,)).fetchone()
        if not row:
            return False
        if row['group_id']:
            group = conn.execute('SELECT permissions FROM permission_groups WHERE id=?',
                                 (row['group_id'],)).fetchone()
            held = permissions.decode(group['permissions']) if group else set()
        else:
            held = permissions.decode(row['legacy']) if row['legacy'] else set(permissions.DEFAULT_PERMISSIONS)
    return bool(set(held) & permissions.ADMIN_PERMISSIONS)


def _reject_privileged(name, keys, actor=''):
    """Privileged accounts are only manageable from 用户管理, which checks the grant permission.

    The 数据管理 console is granted on its own; without this guard that permission alone
    could disable or delete an administrator. Callers that already hold the account
    management permission (`actor`) keep working through this console.
    """
    if name == 'member_accounts' and _privileged(keys, actor):
        raise ValueError('管理员账号只能在用户管理中修改，或由拥有用户管理权限的账号操作')


def update_row(name, keys, values, actor=''):
    """Update whitelisted columns of exactly one row."""
    meta = _table(name)
    if name == 'settings':
        setting_key = str((keys or {}).get('key') or '')
        if setting_key not in WRITABLE_SETTINGS or _is_secret(name, setting_key, None):
            raise ValueError('含凭据或影响站点登录的设置只能通过专用配置入口修改')
    if not meta['editable']:
        raise ValueError('这张表不支持在此修改')
    if not isinstance(values, dict) or not values:
        raise ValueError('没有需要修改的内容')
    with db.write() as conn:
        present = [column for column, _type in columns(conn, name)]
        allowed = [column for column in values if column in meta['editable'] and column in present]
        rejected = [column for column in values if column not in allowed]
        if rejected:
            raise ValueError('不允许修改：' + '、'.join(rejected))
        clause, key_values = _key_clause(meta, present, keys or {})
        _reject_root(name, keys or {})
        _reject_privileged(name, keys or {}, actor)
        assignments = ', '.join(f'{_quote(column)}=?' for column in allowed)
        coerced = [_coerce(conn, name, column, values[column]) for column in allowed]
        changed = conn.execute(
            f'UPDATE {_quote(name)} SET {assignments} WHERE {clause}',
            [*coerced, *key_values]).rowcount
        if not changed:
            raise LookupError('没有找到这一行')
    return {'table': name, 'updated': changed, 'columns': allowed}


def delete_many(name, keys, actor=''):
    """Delete several rows by primary key, skipping any the root account owns."""
    meta = _table(name)
    if not meta.get('deletable', True):
        raise ValueError('这张表不允许删除记录')
    if not keys:
        raise ValueError('没有选中任何记录')
    deleted = 0
    with db.write() as conn:
        present = [column for column, _type in columns(conn, name)]
        for entry in keys:
            try:
                _reject_root(name, entry or {})
                _reject_privileged(name, entry or {}, actor)
                clause, key_values = _key_clause(meta, present, entry or {})
            except ValueError:
                continue  # a malformed or protected row must not abort the batch
            deleted += conn.execute(
                f'DELETE FROM {_quote(name)} WHERE {clause}', key_values).rowcount
    return {'table': name, 'deleted': deleted, 'requested': len(keys)}


def delete_page(name, page=1, size=PAGE_SIZE, q='', actor=''):
    """Delete every row currently visible on one page of one table.

    Runs in a single transaction and reuses the same filters as `preview`, so the rows
    removed are exactly the ones the administrator was looking at.
    """
    meta = _table(name)
    if not meta.get('deletable', True):
        raise ValueError('这张表不允许删除记录')
    page = max(1, int(page or 1))
    size = max(1, min(int(size or PAGE_SIZE), MAX_PAGE_SIZE))
    with db.write() as conn:
        present = [column for column, _type in columns(conn, name)]
        if not present:
            raise LookupError('数据表结构不可用')
        where, params = _filter(name, meta, present, q)
        joined = _join_clause(meta, present)
        source = f'{_quote(name)} t' + ((' ' + joined) if joined else '')
        order = ', '.join('t.' + _quote(column) for column in meta['pk'] if column in present)
        selects = ', '.join('t.' + _quote(column) for column in meta['pk'] if column in present)
        chosen = conn.execute(
            f'SELECT {selects} FROM {source}{where}'
            + (f' ORDER BY {order}' if order else '')
            + ' LIMIT ? OFFSET ?',
            [*params, size, (page - 1) * size]).fetchall()
        deleted = 0
        for row in chosen:
            entry = {column: row[column] for column in meta['pk'] if column in present}
            try:
                _reject_root(name, entry)
                _reject_privileged(name, entry, actor)
                clause, key_values = _key_clause(meta, present, entry)
            except ValueError:
                continue
            deleted += conn.execute(
                f'DELETE FROM {_quote(name)} WHERE {clause}', key_values).rowcount
    return {'table': name, 'deleted': deleted, 'requested': len(chosen)}


def delete_row(name, keys, actor=''):
    """Delete exactly one row by primary key."""
    meta = _table(name)
    if not meta.get('deletable', True):
        raise ValueError('这张表不允许删除记录')
    with db.write() as conn:
        present = [column for column, _type in columns(conn, name)]
        clause, key_values = _key_clause(meta, present, keys or {})
        _reject_root(name, keys or {})
        _reject_privileged(name, keys or {}, actor)
        if name == 'member_accounts' and actor and str(keys.get('username')) == actor:
            raise ValueError('不能在这里删除自己的账号')
        deleted = conn.execute(
            f'DELETE FROM {_quote(name)} WHERE {clause}', key_values).rowcount
        if not deleted:
            raise LookupError('没有找到这一行')
    return {'table': name, 'deleted': deleted}
