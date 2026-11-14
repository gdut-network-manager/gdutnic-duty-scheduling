"""Duty accounts. Administrators are accounts that carry the schedule/accounts/field permissions."""
import hashlib

from server import admin_store, db, permissions as perms_store, weekly_store

INITIAL_PASSWORD = 'gg2017'


def roster_names():
    """Names in the saved weekly table or any existing dated schedule."""
    roster = weekly_store.load()
    names = {name for slot in roster['slots'] for day in slot['days'] for name in day}
    with db.read() as conn:
        names.update(r['name'] for r in conn.execute(
            'SELECT DISTINCT a.name FROM attendance a JOIN shifts s ON s.id=a.shift_id'))
    return {name.strip() for name in names if name.strip()}


def provision():
    """Create missing roster accounts; never reset an existing password."""
    return create_missing(roster_names())


def cleanup_unused():
    """Remove only unassigned default-group accounts, preserving attendance."""
    from server import root_admin
    from server.security import MEMBER_SESSIONS
    perms_store.ensure_defaults()
    with db.write() as conn:
        group = conn.execute('SELECT permissions FROM permission_groups WHERE id=?',
                             (perms_store.DEFAULT_GROUP,)).fetchone()
        if group and perms_store.decode(group['permissions']) & perms_store.ADMIN_PERMISSIONS:
            return {'deleted': 0, 'usernames': []}  # Bulk cleanup never deletes administrators.
        names = roster_names()
        removed = [row['username'] for row in conn.execute(
            'SELECT username FROM member_accounts WHERE group_id=?', (perms_store.DEFAULT_GROUP,))
            if row['username'] not in names and not root_admin.is_root(row['username'])]
        conn.executemany('DELETE FROM member_accounts WHERE username=?', [(name,) for name in removed])
    for name in removed:
        MEMBER_SESSIONS.clear(name + ':')
    return {'deleted': len(removed), 'usernames': sorted(removed)}


def create_missing(names, *, authorize=None):
    """Authorize a new-account grant inside the write transaction, only if needed."""
    names = sorted({n.strip() for n in names if isinstance(n, str) and n.strip()})
    if any(len(n) > 64 for n in names):
        raise ValueError('姓名不能超过 64 字')
    perms_store.ensure_defaults()
    created = 0
    with db.write() as conn:
        accounts = conn.execute('SELECT username,student_id FROM member_accounts').fetchall()
        existing = {row['username'] for row in accounts}
        aliases = {row['student_id'] for row in accounts if row['student_id']}
        missing = [name for name in names if name not in existing]
        if aliases.intersection(missing):
            raise ValueError('姓名与已有账号学号冲突')
        # Hash once per run: every new account starts with the same initial password.
        if missing:
            if authorize is not None:
                authorize()
            hashed = admin_store._hash_password(INITIAL_PASSWORD)
            conn.executemany('INSERT INTO member_accounts(username,password,active,group_id) VALUES(?,?,1,?)',
                             [(name, hashed, perms_store.DEFAULT_GROUP) for name in missing])
            created = len(missing)
    return {'created': created, 'existing': len(names) - created}


def create(name, password=None):
    name = str(name or '').strip()
    if not name or len(name) > 64:
        raise ValueError('请填写姓名，最多 64 字')
    if password is not None and (len(password) < 6 or len(password) > 128):
        raise ValueError('密码长度应为 6～128 位')
    perms_store.ensure_defaults()
    with db.write() as conn:
        if conn.execute('SELECT username FROM member_accounts WHERE username=? OR student_id=?', (name, name)).fetchone():
            raise ValueError('该姓名账号已存在')
        conn.execute('INSERT INTO member_accounts(username,password,active,permissions,group_id) VALUES(?,?,1,?,?)',
                     (name, admin_store._hash_password(password or INITIAL_PASSWORD),
                      perms_store.encode(perms_store.DEFAULT_PERMISSIONS), perms_store.DEFAULT_GROUP))
    return {'created': 1, 'existing': 0}


def delete(name):
    from server.security import MEMBER_SESSIONS
    _reject_root(name, '删除')
    with db.write() as conn:
        if not conn.execute('DELETE FROM member_accounts WHERE username=?', (name,)).rowcount:
            raise LookupError('账号不存在')
    MEMBER_SESSIONS.clear(name + ':')


def list_accounts():
    # The 原始管理员 is deliberately invisible in the account list.
    from server import root_admin
    perms_store.ensure_defaults()
    with db.read() as conn:
        rows = [dict(r) for r in conn.execute(
            'SELECT m.username,m.student_id,m.active,m.group_id,g.name AS group_name,g.permissions, '
            '(k.identity_key IS NOT NULL) AS sso_bound '
            'FROM member_accounts m LEFT JOIN permission_groups g ON g.id=m.group_id '
            'LEFT JOIN keycloak_links k ON k.username=m.username AND k.account_key=m.sso_id ORDER BY m.username')]
    rows = [row for row in rows if not root_admin.is_root(row.get('username'))]
    for row in rows:
        row['permissions'] = sorted(perms_store.decode(row.get('permissions')))
        row['root'] = False
        row['sso_bound'] = bool(row['sso_bound'])
    return rows


def _login_account(value):
    """Fetch one unambiguous local identity, including the credentials to verify."""
    from server import root_admin
    if not value or len(value) > 64 or root_admin.is_root(value):
        return None
    with db.read() as conn:
        rows = conn.execute(
            'SELECT username,password,active FROM member_accounts '
            'WHERE username=? OR student_id=? LIMIT 2', (value, value)).fetchall()
    # Imported/externally edited data may contain conflicting aliases. Fail closed.
    if len(rows) != 1 or root_admin.is_root(rows[0]['username']):
        return None
    return rows[0]


def resolve_login_name(value):
    """Canonical name for account-level login throttling; never a session identity."""
    row = _login_account(value)
    return row['username'] if row else value


def _verified_version(row, password):
    if row and row['active'] and admin_store._verify_password(password, row['password']):
        return hashlib.sha256(row['password'].encode()).hexdigest()
    return None


def authenticate_login(value, password):
    """Validate a username or student ID against the same fetched account record."""
    row = _login_account(value)
    version = _verified_version(row, password)
    return (row['username'], version) if version else None


def set_student_id(name, student_id):
    with db.write() as conn:
        _set_student_id(conn, name, student_id)


def _set_student_id(conn, name, student_id):
    from server import root_admin
    _reject_root(name, '在用户管理中修改')
    if not isinstance(student_id, str):
        raise ValueError('学号必须为文本')
    student_id = student_id.strip()
    if len(student_id) > 64 or any(ord(c) < 32 or ord(c) == 127 for c in student_id):
        raise ValueError('学号最多 64 字，不能包含控制字符')
    if not conn.execute('SELECT username FROM member_accounts WHERE username=?', (name,)).fetchone():
        raise LookupError('账号不存在')
    if student_id and (root_admin.is_root(student_id) or conn.execute(
            'SELECT username FROM member_accounts WHERE username<>? AND (username=? OR student_id=?)',
            (name, student_id, student_id)).fetchone()):
        raise ValueError('学号已被使用或与其他账号用户名冲突')
    conn.execute('UPDATE member_accounts SET student_id=? WHERE username=?', (student_id, name))


def authenticate(name, password):
    from server import root_admin
    if root_admin.is_root(name):
        return None  # the root identity only authenticates against its own file
    with db.read() as conn:
        row = conn.execute('SELECT password,active FROM member_accounts WHERE username=?', (name,)).fetchone()
    return _verified_version(row, password)


def verify(name, password):
    return authenticate(name, password) is not None


def session_valid(name, version, *, use_cache=True):
    # Changing/resetting a password invalidates pre-existing member sessions. The cache
    # key includes the stored digest, so a new password simply misses the old entry.
    from server.security import MEMBER_SESSIONS
    from server import root_admin
    if root_admin.is_root(name):
        return version == root_admin.session_version()
    key = f'{name}:{version}'
    cached = MEMBER_SESSIONS.get(key) if use_cache else None
    if cached is not None:
        return cached
    with db.read() as conn:
        row = conn.execute('SELECT password,active FROM member_accounts WHERE username=?', (name,)).fetchone()
    valid = bool(row and row['active'] and version == hashlib.sha256(row['password'].encode()).hexdigest())
    if use_cache:
        MEMBER_SESSIONS.put(key, valid)
    return valid


def session_version(name):
    from server import root_admin
    if root_admin.is_root(name):
        return root_admin.session_version()  # admin sessions follow the file, not the table
    with db.read() as conn:
        row = conn.execute('SELECT password FROM member_accounts WHERE username=?', (name,)).fetchone()
    return hashlib.sha256(row['password'].encode()).hexdigest()


def _reject_root(name, action):
    """Storage-level guard: the root account is never a member_accounts row."""
    from server import root_admin
    if root_admin.is_root(name):
        raise ValueError(f'原始管理员不能{action}')


def update(name, active=None, reset=False):
    from server.security import MEMBER_SESSIONS
    _reject_root(name, '在用户管理中修改')
    with db.write() as conn:
        if not conn.execute('SELECT username FROM member_accounts WHERE username=?', (name,)).fetchone():
            raise ValueError('账号不存在')
        if active is not None:
            conn.execute('UPDATE member_accounts SET active=? WHERE username=?', (int(bool(active)), name))
        if reset:
            conn.execute('UPDATE member_accounts SET password=? WHERE username=?',
                         (admin_store._hash_password(INITIAL_PASSWORD), name))
    MEMBER_SESSIONS.clear(name + ':')


def set_password(name, new_password):
    """Administrator-side password change; no old password required."""
    from server.security import MEMBER_SESSIONS
    _reject_root(name, '在这里修改密码')
    if len(new_password) < 6 or len(new_password) > 128:
        raise ValueError('新密码长度应为 6～128 位')
    with db.write() as conn:
        if not conn.execute('UPDATE member_accounts SET password=? WHERE username=?',
                            (admin_store._hash_password(new_password), name)).rowcount:
            raise LookupError('账号不存在')
    MEMBER_SESSIONS.clear(name + ':')


def change_password(name, old_password, new_password):
    from server.security import MEMBER_SESSIONS
    _reject_root(name, '在这里修改密码')
    if len(new_password) < 6 or len(new_password) > 128:
        raise ValueError('新密码长度应为 6～128 位')
    with db.write() as conn:
        row = conn.execute('SELECT password,active FROM member_accounts WHERE username=?', (name,)).fetchone()
        if not row or not row['active'] or not admin_store._verify_password(old_password, row['password']):
            raise ValueError('当前密码不正确')
        conn.execute('UPDATE member_accounts SET password=? WHERE username=?',
                     (admin_store._hash_password(new_password), name))
    MEMBER_SESSIONS.clear(name + ':')
