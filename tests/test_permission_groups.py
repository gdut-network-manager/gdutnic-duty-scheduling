"""Group membership, shared access updates, migrations, and API enforcement."""
import sqlite3
from fastapi.testclient import TestClient
from test_permissions import PermissionFixture
from server import db, permissions, member_store, root_admin, admin_store, schedule_store


class TestPermissionGroups(PermissionFixture):
    def client(self, grants):
        member_store.create('operator', 'password123')
        permissions.set_permissions('operator', grants)
        admin_store.remember_setup('operator')
        client = TestClient(__import__('server.app', fromlist=['app']).app)
        client.get('/' + admin_store.path())
        self.assertEqual(client.post('/api/admin/login', json={'username':'operator','password':'password123'}).status_code, 200)
        return client

    def test_default_group_for_all_new_account_creation_paths(self):
        member_store.create('one')
        member_store.create_missing(['two', 'three'])
        accounts = member_store.list_accounts()
        self.assertEqual({a['group_id'] for a in accounts}, {'member'})
        self.assertEqual({a['group_name'] for a in accounts}, {'会员'})
        self.assertEqual(permissions.permissions_of('one'), set(permissions.DEFAULT_PERMISSIONS))
        self.assertEqual(len(permissions.list_groups()), 1)

    def test_editing_default_changes_current_and_future_members_without_reset(self):
        member_store.create('one')
        permissions.save_group('会员', ['duty'], 'member')
        member_store.create('two')
        member_store.create_missing(['three'])
        permissions.ensure_defaults()
        for name in ('one','two','three'):
            self.assertEqual(permissions.permissions_of(name), {'duty'})

    def test_shared_group_edits_update_every_member_and_keep_passwords(self):
        for name in ('one','two'): member_store.create(name, 'password123')
        group = permissions.save_group('外勤组', ['member_field'])
        for name in ('one','two'): permissions.set_group(name, group['id'])
        permissions.save_group('外勤负责人', ['field_confirm','field'], group['id'])
        for name in ('one','two'):
            self.assertEqual(permissions.permissions_of(name), {'field_confirm','field'})
            self.assertTrue(member_store.verify(name, 'password123'))
        permissions.set_group('one', group['id'])  # selecting the current group is valid on MySQL too
        self.assertEqual(next(g for g in permissions.list_groups() if g['id']==group['id'])['member_count'], 2)

    def test_delete_transfers_members_and_protects_default(self):
        member_store.create('one')
        group = permissions.save_group('临时组', ['field'])
        permissions.set_group('one', group['id'])
        self.assertEqual(permissions.delete_group(group['id']), {'moved':1})
        self.assertEqual(member_store.list_accounts()[0]['group_id'], 'member')
        with self.assertRaises(ValueError): permissions.delete_group('member')

    def test_root_remains_file_only_and_has_every_page(self):
        root_admin.create_if_missing('password123')
        permissions.ensure_defaults()
        self.assertEqual(permissions.permissions_of(root_admin.USERNAME), set(permissions.ALL_PERMISSIONS))
        self.assertIn('settings', permissions.permissions_of(root_admin.USERNAME))
        with self.assertRaises(ValueError): permissions.set_group(root_admin.USERNAME, 'member')
        self.assertEqual(member_store.list_accounts(), [])

    def test_group_input_validation_and_duplicates(self):
        permissions.save_group('外勤组', [])
        with self.assertRaises(ValueError): permissions.save_group('外勤组', ['field'])
        for name, values in [('', []), ('x'*65, []), ('未知组', ['unknown']), ('组', 'field'), ('组', [{}])]:
            with self.assertRaises(ValueError): permissions.save_group(name, values)
        with self.assertRaises(LookupError): permissions.set_group('missing', 'member')
        member_store.create('one')
        with self.assertRaises(LookupError): permissions.set_group('one', 'missing')
        with self.assertRaises(ValueError): permissions.save_group('改名', [], 'member')

    def test_old_sqlite_schema_and_permissions_upgrade_idempotently(self):
        # Build a genuine pre-group table before db.connect() upgrades columns.
        with sqlite3.connect(db.DB_PATH) as conn:
            conn.execute("CREATE TABLE member_accounts(username TEXT PRIMARY KEY,password TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,permissions TEXT NOT NULL DEFAULT '')")
            conn.executemany('INSERT INTO member_accounts VALUES(?,?,1,?)',
                             [('old','hash','user,schedule'),('new','hash','user')])
        permissions.ensure_defaults()
        # `schedule` also unlocks 其他设置, which is derived from rules + clock.
        expected = set(permissions.DEFAULT_PERMISSIONS) | set(permissions.LEGACY['schedule'])
        if expected & set(permissions.DERIVED['settings']):
            expected.add('settings')
        self.assertEqual(permissions.permissions_of('old'), expected)
        self.assertEqual(permissions.permissions_of('new'), set(permissions.DEFAULT_PERMISSIONS))
        count = len(permissions.list_groups())
        permissions.ensure_defaults()
        self.assertEqual(len(permissions.list_groups()), count)

    def test_group_crud_and_membership_api_rejects_per_user_options(self):
        client = self.client(permissions.ALL_PERMISSIONS)
        member_store.create('one')
        result = client.post('/api/admin/permission-groups', json={'name':'外勤组', 'permissions':['field']})
        self.assertEqual(result.status_code, 200)
        group = result.json()
        self.assertEqual(client.put('/api/admin/members/permissions', json={'username':'one','group_id':group['id']}).status_code, 200)
        self.assertEqual(client.put('/api/admin/members/permissions', json={'username':'one','permissions':['data']}).status_code, 400)
        self.assertEqual(client.put('/api/admin/permission-groups/'+group['id'], json={'name':'外勤组','permissions':['field','logs']}).status_code, 200)
        self.assertEqual(permissions.permissions_of('one'), {'field','logs'})
        self.assertEqual(client.delete('/api/admin/permission-groups/'+group['id']).status_code, 200)
        self.assertEqual(client.delete('/api/admin/permission-groups/member').status_code, 400)
        self.assertEqual(client.put('/api/admin/members/permissions', json={'username':'one','group_id':'missing'}).status_code, 404)

    def test_each_admin_sidebar_option_has_an_independent_api_gate(self):
        client = self.client(['permission_groups'])
        routes = {'overview':'/api/admin/overview',
                  'weekly':'/api/admin/weekly-roster',
                  'special':'/api/special-schedules','swap':'/api/admin/swaps','members':'/api/admin/members',
                  'field':'/api/admin/field-duty','rules':'/api/admin/rules','clock':'/api/admin/clock',
                  'logs':'/api/admin/logs',
                  'data':'/api/admin/data/tables','permission_groups':'/api/admin/permission-groups'}
        for granted in routes:
            permissions.set_permissions('operator', [granted])
            for permission, route in routes.items():
                self.assertEqual(client.get(route).status_code, 200 if granted==permission else 403, (granted, route))
        # 更改考勤状态 is a write-only gate: without it the endpoint must refuse the write.
        shift_id = schedule_store.save(dict(day='2026-11-02', start='09:00', end='10:00', members=['甲']))
        for granted, expected in (('attendance_edit', 200), ('overview', 403)):
            permissions.set_permissions('operator', [granted])
            response = client.put('/api/admin/overview/attendance',
                                  json={'shift_id': shift_id, 'name': '甲', 'status': 'late'})
            self.assertEqual(response.status_code, expected, granted)

    def test_member_sidebar_permissions_and_live_group_revocation(self):
        member_store.create('one', 'password123')
        app = __import__('server.app',fromlist=['app']).app
        client = TestClient(app)
        self.assertEqual(client.post('/api/member/login', json={'username':'one','password':'password123'}).status_code, 200)
        group = permissions.save_group('仅签到', ['duty'])
        permissions.set_group('one', group['id'])
        self.assertEqual(client.get('/api/schedules?day=2026-11-02').status_code, 200)
        self.assertEqual(client.get('/api/member/field-duty').status_code, 200)
        self.assertEqual(client.post('/api/member/field-duty').status_code, 403)
        self.assertEqual(client.get('/api/member/field-duty/pending').status_code, 403)
        permissions.save_group('仅签到', ['member_field'], group['id'])
        self.assertEqual(client.get('/api/schedules?day=2026-11-02').status_code, 403)
        self.assertEqual(client.get('/api/member/field-duty').status_code, 200)
        self.assertEqual(client.get('/api/member/status').json()['permissions'], ['member_field'])

    def test_data_console_cannot_write_raw_permissions_or_group_membership(self):
        from server import data_admin
        member_store.create('one')
        for values in ({'permissions':'data'}, {'group_id':'fake'}):
            with self.assertRaises(ValueError): data_admin.update_row('member_accounts', {'username':'one'}, values)

    def test_console_recovery_does_not_reuse_an_edited_legacy_group(self):
        member_store.create('one')
        permissions.set_permissions('one', permissions.ALL_PERMISSIONS)
        group = next(g for g in permissions.list_groups() if not g['default'])
        permissions.save_group(group['name'], ['field'], group['id'])
        self.assertEqual(permissions.permissions_of('one'), {'field'})
        permissions.set_permissions('one', permissions.ALL_PERMISSIONS)
        self.assertEqual(permissions.permissions_of('one'), set(permissions.ALL_PERMISSIONS))
        self.assertEqual(next(g for g in permissions.list_groups() if g['id']==group['id'])['permissions'], ['field'])


class TestGranularPermissions(TestPermissionGroups):
    """概览内改判、值班时间、系统时间各自独立成页权限。"""
    # The parent's cases already run once; this class adds only the new ones.
    test_group_crud_and_membership_api_rejects_per_user_options = None
    test_each_admin_sidebar_option_has_an_independent_api_gate = None
    test_member_sidebar_permissions_and_live_group_revocation = None
    test_data_console_cannot_write_raw_permissions_or_group_membership = None
    test_root_remains_file_only_and_has_every_page = None
    test_old_sqlite_schema_and_permissions_upgrade_idempotently = None

    def test_overview_and_attendance_edit_are_separate(self):
        client = self.client(['overview'])
        shift_id = schedule_store.save(dict(day='2026-11-02', start='09:00', end='10:00', members=['甲']))
        # 只读概览：能看，不能改判。
        self.assertEqual(client.get('/api/admin/overview').status_code, 200)
        denied = client.put('/api/admin/overview/attendance',
                            json={'shift_id': shift_id, 'name': '甲', 'status': 'late'})
        self.assertEqual(denied.status_code, 403)
        # 只有改判权限：能改，不需要概览的读权限。
        permissions.set_permissions('operator', ['attendance_edit'])
        allowed = client.put('/api/admin/overview/attendance',
                             json={'shift_id': shift_id, 'name': '甲', 'status': 'late'})
        self.assertEqual(allowed.status_code, 200, allowed.text)

    def test_rules_and_clock_are_independent_of_the_settings_page(self):
        client = self.client(['rules'])
        self.assertEqual(client.get('/api/admin/rules').status_code, 200)
        self.assertEqual(client.get('/api/admin/clock').status_code, 403)
        self.assertEqual(client.put('/api/admin/clock', json={'automatic': True}).status_code, 403)
        permissions.set_permissions('operator', ['clock'])
        self.assertEqual(client.get('/api/admin/clock').status_code, 200)
        self.assertEqual(client.get('/api/admin/rules').status_code, 403)

    def test_settings_is_derived_from_its_children_only(self):
        # 「其他设置」没有独立入口：只有持有子项才有访问权，也不能单独授予。
        for values in (['settings'], ['settings', 'rules']):
            with self.assertRaises(ValueError):
                permissions.save_group('非法组', values)
        self.assertEqual(permissions.encode({'settings', 'rules'}), 'rules')
        alone = permissions.save_group('无设置权', ['overview'])
        member_store.create('one')
        permissions.set_group('one', alone['id'])
        self.assertEqual(permissions.permissions_of('one'), {'overview'})
        with_rules = permissions.save_group('可看值班时间', ['rules'])
        permissions.set_group('one', with_rules['id'])
        self.assertIn('settings', permissions.permissions_of('one'))

    def test_tree_groups_the_new_leaves(self):
        tree = {node['id']: node for node in permissions.TREE}
        overview = next(n for n in tree['admin_area']['children'] if n['id'] == 'overview_group')
        self.assertEqual({leaf['id'] for leaf in overview['children']}, {'overview', 'attendance_edit'})
        settings = next(n for n in tree['admin_area']['children'] if n['id'] == 'settings_group')
        self.assertEqual({leaf['id'] for leaf in settings['children']}, {'rules', 'clock', 'smtp', 'checkin_manage', 'sso'})
        # Group ids are never stored; 其他设置 is derived, not a tree leaf.
        self.assertEqual(set(permissions.leaves()),
                         set(permissions.PERMISSIONS) - set(permissions.DERIVED))
        self.assertNotIn('settings', permissions.leaves())

    def test_new_group_can_grant_only_the_new_leaves(self):
        group = permissions.save_group('考勤管理员', ['overview', 'attendance_edit'])
        member_store.create('one')
        permissions.set_group('one', group['id'])
        self.assertEqual(permissions.permissions_of('one'), {'overview', 'attendance_edit'})
