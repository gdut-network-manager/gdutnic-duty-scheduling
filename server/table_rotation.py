"""Size cap for record tables that only ever grow.

Each table is measured on its own, the same way as the operation log: once its stored
text passes LIMIT_BYTES the oldest rows are deleted until it is back under KEEP_BYTES.
"""
from __future__ import annotations

from server import db, field_duty, swap_store

LIMIT_BYTES = 10 * 1024 * 1024
KEEP_BYTES = 7 * 1024 * 1024
BATCH = 500


def _revoke_sso_logins(conn, rows):
    # A session opened through a binding must not outlive that binding.
    conn.executemany("DELETE FROM login_sessions WHERE username=? AND sso_identity<>''",
                     [(row['username'],) for row in rows])


# `order` lists the columns that put the oldest row first.
TABLES = {
    'attendance_history': {
        'pk': ('id',), 'order': ('id',),
        'columns': ('day', 'start', 'end', 'name', 'checked_at', 'location', 'note', 'archived_at', 'final_status')},
    'field_duty': {
        'pk': ('month', 'username'), 'order': ('month', 'registered_at', 'username'),
        'columns': ('month', 'username', 'registered_at', 'manual', 'status'), 'init': field_duty.init},
    'swaps': {
        'pk': ('id',), 'order': ('id',),
        'columns': ('shift_a', 'name_a', 'shift_b', 'name_b', 'created_at'), 'init': swap_store.init},
    'push_deliveries': {
        'pk': ('id',), 'order': ('updated_at', 'id'),
        'columns': ('id', 'period_end', 'recipient', 'state', 'updated_at', 'error')},
    'keycloak_links': {
        'pk': ('identity_key',), 'order': ('linked_at', 'identity_key'),
        'columns': ('identity_key', 'issuer', 'subject', 'username', 'account_key', 'linked_at'),
        'after': _revoke_sso_logins},
}


def _size(columns):
    return '+'.join(f'COALESCE(LENGTH(`{column}`),0)' for column in columns)


def used_bytes(conn, table):
    return conn.execute(f'SELECT COALESCE(SUM({_size(TABLES[table]["columns"])}),0) AS n FROM `{table}`').fetchone()['n']


def _prune(conn, table):
    spec = TABLES[table]
    if 'init' in spec:
        spec['init'](conn)
    used = used_bytes(conn, table)
    if used <= LIMIT_BYTES:
        return 0
    target, freed, removed = used - KEEP_BYTES, 0, 0
    select = (f'SELECT *, {_size(spec["columns"])} AS row_bytes FROM `{table}`'
              f' ORDER BY {",".join(f"`{column}`" for column in spec["order"])} LIMIT {BATCH}')
    delete = f'DELETE FROM `{table}` WHERE ' + ' AND '.join(f'`{column}`=?' for column in spec['pk'])
    while freed < target:
        cut = []
        for row in conn.execute(select).fetchall():
            cut.append(row)
            freed += row['row_bytes'] or 0
            if freed >= target:
                break
        if not cut:
            break
        conn.executemany(delete, [tuple(row[column] for column in spec['pk']) for row in cut])
        if 'after' in spec:
            spec['after'](conn, cut)
        removed += len(cut)
    return removed


def prune():
    """Trim every capped table that is over the limit. Returns rows removed per table."""
    removed = {}
    for table in TABLES:
        with db.write() as conn:
            count = _prune(conn, table)
        if count:
            removed[table] = count
    return removed
