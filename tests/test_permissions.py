import unittest

from fastapi.testclient import TestClient

import test_schedule
from server import admin_store, member_store, permissions


class PermissionFixture(test_schedule.ScheduleTest):
    """Shared SQLite fixture only; its schedule cases are not inherited below."""
    test_lifecycle_and_repeat_safe_attendance = None
    test_reject_wrong_person_and_time = None
    test_invalid_shift = None
    test_member_only_sees_own_shifts_and_attendance = None
    test_location_is_optional = None
    test_duty_rules_control_check_in_window = None
    test_special_shifts_are_listed_and_edited = None
    test_check_in_chains_only_pairs_1_2_and_3_4 = None
    test_pairs_use_day_order_not_only_this_person_shifts = None
    test_chained_shifts_stay_on_time_while_first_keeps_lateness = None
    test_overnight_pair_does_not_chain_into_next_day = None
    test_overnight_appears_and_signs_in_after_midnight = None


class TestPermissions(PermissionFixture):
    """Permission groups decide who may use which administrator endpoint."""

    def admin(self, name, password, granted):
        member_store.create(name, password)
        permissions.set_permissions(name, granted)
        admin_store.remember_setup(name)

    def client_for(self, name, password):
        client = TestClient(__import__('server.app', fromlist=['app']).app)
        client.get('/' + admin_store.path())
        response = client.post('/api/admin/login', json={'username': name, 'password': password})
        self.assertEqual(response.status_code, 200, response.text)
        return client

    def test_permission_encoding_and_defaults(self):
        self.assertEqual(permissions.decode(permissions.encode(['field', 'duty'])), {'duty', 'field'})
        self.assertEqual(permissions.decode('bogus,duty'), {'duty'})
        self.assertEqual(set(permissions.DEFAULT_PERMISSIONS), {'duty', 'member_field', 'field_confirm'})
        self.assertTrue(permissions.is_admin('x') is False)

    def test_first_administrator_gets_every_permission(self):
        self.admin('admin', 'password123', permissions.ADMIN_DEFAULTS)
        self.assertEqual(permissions.permissions_of('admin'), set(permissions.ALL_PERMISSIONS))

    def test_schedule_only_account_cannot_touch_accounts(self):
        self.admin('admin', 'password123', permissions.ADMIN_DEFAULTS)
        self.admin('planner', 'password123', ['user', 'schedule'])
        client = self.client_for('planner', 'password123')
        self.assertEqual(client.get('/api/admin/overview').status_code, 200)
        self.assertEqual(client.get('/api/admin/members').status_code, 403)
        self.assertEqual(client.put('/api/admin/rules', json={'early_minutes': 5}).status_code, 200)

    def test_field_only_account_is_limited_to_field_duty(self):
        self.admin('admin', 'password123', permissions.ADMIN_DEFAULTS)
        self.admin('scout', 'password123', ['user', 'field'])
        client = self.client_for('scout', 'password123')
        self.assertEqual(client.get('/api/admin/field-duty').status_code, 200)
        self.assertEqual(client.get('/api/admin/overview').status_code, 403)
        self.assertEqual(client.delete('/api/admin/schedules').status_code, 403)

    def test_plain_member_cannot_use_admin_pages(self):
        self.admin('admin', 'password123', permissions.ADMIN_DEFAULTS)
        member_store.create('member', 'password123')
        client = TestClient(__import__('server.app', fromlist=['app']).app)
        client.get('/' + admin_store.path())
        response = client.post('/api/admin/login', json={'username': 'member', 'password': 'password123'})
        self.assertEqual(response.status_code, 403)
        # The same account still signs in to the member page.
        self.assertEqual(client.post('/api/member/login', json={'username': 'member', 'password': 'password123'}).status_code, 200)

    def test_password_login_matches_member_credentials(self):
        self.admin('admin', 'password123', permissions.ADMIN_DEFAULTS)
        self.assertIsNotNone(member_store.authenticate('admin', 'password123'))
        self.assertIsNone(member_store.authenticate('admin', 'wrong-password'))

    def test_members_option_controls_account_management_and_keeps_root_protected(self):
        from server import root_admin
        root_admin.create_if_missing('root-password-123')
        self.admin('staff', 'password123', ['members'])
        client = self.client_for('staff', 'password123')
        member_store.create('ordinary', 'password123')
        self.assertEqual(client.patch('/api/admin/members', json={'username': 'ordinary', 'active': False}).status_code, 200)
        self.assertEqual(client.patch('/api/admin/members', json={'username': root_admin.USERNAME, 'reset_password': True}).status_code, 403)
        self.assertEqual(client.delete('/api/admin/members', params={'username': root_admin.USERNAME}).status_code, 403)
        self.assertEqual(client.put('/api/admin/members/permissions', json={'username': 'ordinary', 'permissions': ['field']}).status_code, 400)
        self.assertEqual(client.get('/api/admin/permission-groups').status_code, 403)

    def test_grant_holder_can_change_permissions_and_passwords(self):
        self.admin('root', 'password123', permissions.ADMIN_DEFAULTS)
        member_store.create('target', 'password123')
        owner = self.client_for('root', 'password123')
        self.assertEqual(owner.put('/api/admin/members/permissions',
                                   json={'username': 'target', 'group_id': permissions.save_group('排班负责人', ['overview'])['id']}).status_code, 200)
        self.assertTrue(permissions.has('target', 'overview'))
        self.assertEqual(owner.put('/api/admin/members/password',
                                   json={'username': 'target', 'password': 'new-password'}).status_code, 200)
        self.assertTrue(member_store.verify('target', 'new-password'))
        self.assertFalse(member_store.verify('target', 'password123'))
        # The account itself may set its own password; others need the grant permission.
        self.assertEqual(owner.put('/api/admin/members/password',
                                   json={'username': 'root', 'password': 'another-pass'}).status_code, 200)
        self.assertTrue(member_store.verify('root', 'another-pass'))
        # Members-page access includes account operations; unrelated page access does not.
        self.admin('operator', 'password123', ['duty', 'field'])
        operator = self.client_for('operator', 'password123')
        self.assertEqual(operator.put('/api/admin/members/password',
                                      json={'username': 'target', 'password': 'hijack-pass'}).status_code, 403)
        self.assertFalse(member_store.verify('target', 'hijack-pass'))

    def test_legacy_admin_identity_is_now_the_root_account(self):
        # 旧版管理员不再是一个独立账号：该身份就是原始管理员，权限固定且不出现在列表里。
        from server import db, root_admin
        root_admin.create_if_missing('root-secret-123')
        admin_store.register('legacy', 'password123')
        admin_store.remember_setup('legacy')
        member_store.create('ordinary', 'password123')
        with db.write() as conn:
            conn.execute("UPDATE member_accounts SET permissions=''")
        permissions.ensure_defaults()
        self.assertEqual(permissions.permissions_of(root_admin.username()),
                         set(permissions.PERMISSIONS))
        self.assertEqual(permissions.permissions_of('ordinary'), set(permissions.DEFAULT_PERMISSIONS))
        self.assertTrue(member_store.verify('ordinary', 'password123'))
        # The old console name is gone rather than left as a second administrator.
        self.assertFalse(member_store.verify('legacy', 'password123'))
        self.assertNotIn('legacy', [item['username'] for item in member_store.list_accounts()])

    def test_grant_admin_command_restores_full_access(self):
        from server import grant_admin
        member_store.create('AndyZhang123', 'password123')
        result = grant_admin.grant('AndyZhang123')
        self.assertEqual(set(result['permissions']), set(permissions.ALL_PERMISSIONS))
        self.assertTrue(permissions.is_admin('AndyZhang123'))
        with self.assertRaises(LookupError):
            grant_admin.grant('不存在的人')

    def test_members_page_is_one_independent_option(self):
        self.admin('ops', 'password123', ['members'])
        client = self.client_for('ops', 'password123')
        self.assertEqual(client.post('/api/admin/members', json={'username': 'newbie'}).status_code, 200)
        self.assertEqual(client.patch('/api/admin/members', json={'username': 'newbie', 'active': False}).status_code, 200)
        self.assertEqual(client.delete('/api/admin/members', params={'username': 'newbie'}).status_code, 200)
        self.assertEqual(client.get('/api/admin/overview').status_code, 403)
        self.assertEqual(client.get('/api/admin/permission-groups').status_code, 403)

    def test_permission_tree_covers_every_leaf(self):
        # 其他设置是派生页选择器，不出现在树上，也不能单独授予。
        self.assertEqual(set(permissions.leaves()), set(permissions.ALL_PERMISSIONS) - {'settings'})
        self.assertEqual(set(permissions.GRANTABLE) - set(permissions.DERIVED),
                         set(permissions.ALL_PERMISSIONS) - set(permissions.DERIVED))
        self.assertTrue(set(permissions.ADMIN_PERMISSIONS) <= set(permissions.ALL_PERMISSIONS))
        self.assertNotIn('user', permissions.ADMIN_PERMISSIONS)

    def test_losing_admin_permissions_ends_the_session(self):
        self.admin('root', 'password123', permissions.ADMIN_DEFAULTS)
        self.admin('temp', 'password123', ['user', 'schedule'])
        client = self.client_for('temp', 'password123')
        self.assertEqual(client.get('/api/admin/overview').status_code, 200)
        permissions.set_permissions('temp', ['user'])
        self.assertEqual(client.get('/api/admin/overview').status_code, 401)


class TestLegacyAdminsFoldIntoRoot(test_schedule.ScheduleTest):
    """旧版 users 行只代表原始管理员，不再生成第二个可登录管理员账号。"""
    test_lifecycle_and_repeat_safe_attendance = None
    test_reject_wrong_person_and_time = None
    test_invalid_shift = None
    test_member_only_sees_own_shifts_and_attendance = None
    test_location_is_optional = None
    test_duty_rules_control_check_in_window = None
    test_special_shifts_are_listed_and_edited = None
    test_check_in_chains_only_pairs_1_2_and_3_4 = None
    test_pairs_use_day_order_not_only_this_person_shifts = None
    test_chained_shifts_stay_on_time_while_first_keeps_lateness = None
    test_overnight_pair_does_not_chain_into_next_day = None
    test_overnight_appears_and_signs_in_after_midnight = None

    def test_legacy_user_rows_do_not_become_manageable_accounts(self):
        from server import db, member_store, permissions, root_admin
        root_admin.create_if_missing('root-secret-123')
        with db.write() as conn:
            conn.execute("INSERT INTO users(username,password) VALUES('oldroot','hash')")
        permissions.ensure_defaults()
        names = [item['username'] for item in member_store.list_accounts()]
        assert 'oldroot' not in names
        assert root_admin.username() not in names

    def test_existing_member_account_is_never_deleted(self):
        # 旧 users 行可能只是一个真实值班账号；迁移绝不能删除它。
        from server import db, member_store, permissions, root_admin
        root_admin.create_if_missing('root-secret-123')
        member_store.create('AndyZhang123')
        with db.write() as conn:
            conn.execute("INSERT INTO users(username,password) VALUES('AndyZhang123','hash')")
        permissions.ensure_defaults()
        names = [item['username'] for item in member_store.list_accounts()]
        assert 'AndyZhang123' in names, '迁移不得删除真实账号'
        assert root_admin.username() not in names

    def test_root_row_is_purged_from_the_account_table(self):
        from server import db, root_admin
        root_admin.create_if_missing('root-secret-123')
        with db.write() as conn:
            conn.execute("INSERT INTO member_accounts(username,password,active,permissions)"
                         " VALUES(?,?,1,'user')", (root_admin.username(), 'x'))
        permissions.ensure_defaults()
        with db.read() as conn:
            assert conn.execute('SELECT username FROM member_accounts WHERE username=?',
                                (root_admin.username(),)).fetchone() is None

    def test_real_duty_account_is_kept(self):
        from server import db, member_store, permissions, root_admin, schedule_store
        root_admin.create_if_missing('root-secret-123')
        member_store.create_missing(['张三'])
        # A duty account that happens to share a legacy admin's name must survive.
        with db.write() as conn:
            conn.execute("INSERT INTO users(username,password) VALUES('张三','hash')")
        schedule_store.save(dict(day='2026-11-02', start='09:00', end='10:00', members=['张三']))
        permissions.ensure_defaults()
        assert '张三' in [item['username'] for item in member_store.list_accounts()]
