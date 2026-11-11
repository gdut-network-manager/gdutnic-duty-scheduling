"""The 原始管理员: one fixed account, stored outside the database, impossible to weaken."""
import json
import os
import unittest

import test_schedule
from server import admin_store, db, member_store, permissions, root_admin


class TestRootAdmin(test_schedule.ScheduleTest):
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

    def test_credentials_live_in_their_own_private_file(self):
        assert not root_admin.exists()
        created = root_admin.create_if_missing('root-secret-123')
        assert created['created'] is True
        source = root_admin.path()
        assert source.name == 'root_admin.json'
        assert source.parent == __import__('server.db', fromlist=['db']).DATA
        saved = json.loads(source.read_text(encoding='utf-8'))
        assert saved['username'] == root_admin.USERNAME
        assert saved['password'] != 'root-secret-123', '密码必须哈希保存'
        if os.name != 'nt':
            assert source.stat().st_mode & 0o777 == 0o600

    def test_created_only_once(self):
        root_admin.create_if_missing('first-password')
        again = root_admin.create_if_missing('second-password')
        assert again['created'] is False
        assert root_admin.verify('first-password') is True
        assert root_admin.verify('second-password') is False

    def test_name_is_fixed_and_cannot_be_renamed(self):
        root_admin.create_if_missing('root-secret-123')
        assert root_admin.username() == '原始管理员'
        assert not hasattr(root_admin, 'rename')
        # Unknown names are ordinary accounts, never the root.
        assert root_admin.is_root('原始管理员') is True
        assert root_admin.is_root('root') is False
        assert root_admin.is_root('') is False

    def test_operator_can_choose_the_initial_name(self):
        created = root_admin.create_if_missing('root-secret-123', '  协会负责人  ')
        assert created == {'created': True, 'username': '协会负责人'}, created
        assert root_admin.username() == '协会负责人'
        assert root_admin.is_root('协会负责人') is True
        assert root_admin.is_root('原始管理员') is False
        assert root_admin.verify('root-secret-123') is True

    def test_blank_name_falls_back_to_the_built_in_one(self):
        for value in (None, '', '   '):
            with self.subTest(value=value):
                self.delete_root_file()
                assert root_admin.create_if_missing('root-secret-123', value)['username'] == root_admin.USERNAME
                assert root_admin.username() == root_admin.USERNAME

    def test_initial_name_is_validated(self):
        for value in ('x' * 65, 'bad\x00name', 'line\nbreak'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                root_admin.create_if_missing('root-secret-123', value)
        assert not root_admin.exists(), 'rejected names must not create a record'

    def test_a_later_call_cannot_change_the_chosen_name(self):
        root_admin.create_if_missing('root-secret-123', '协会负责人')
        again = root_admin.create_if_missing('other-password', 'someone-else')
        assert again == {'created': False, 'username': '协会负责人'}

    def delete_root_file(self):
        try:
            root_admin.path().unlink()
        except OSError:
            pass

    def test_password_can_only_be_rotated_through_the_root_module(self):
        root_admin.create_if_missing('root-secret-123')
        root_admin.change_password('new-password-456')
        assert root_admin.verify('new-password-456') is True
        assert root_admin.verify('root-secret-123') is False
        with self.assertRaises(ValueError):
            root_admin.change_password('short')

    def test_always_holds_every_permission(self):
        root_admin.create_if_missing('root-secret-123')
        name = root_admin.username()
        assert permissions.permissions_of(name) == set(permissions.PERMISSIONS)
        assert permissions.is_admin(name) is True
        for leaf in permissions.ALL_PERMISSIONS:
            assert permissions.has(name, leaf)
        with self.assertRaises(ValueError):
            permissions.set_permissions(name, [])

    def test_hidden_from_the_account_list(self):
        root_admin.create_if_missing('root-secret-123')
        member_store.create('张三')
        names = [item['username'] for item in member_store.list_accounts()]
        assert '张三' in names
        assert root_admin.username() not in names

    def test_hidden_even_if_a_row_is_planted(self):
        # A stray row with the fixed name must still never reach 用户管理.
        root_admin.create_if_missing('root-secret-123')
        with db.write() as conn:
            conn.execute("INSERT INTO member_accounts(username,password,active,permissions)"
                         " VALUES(?,?,1,'user')", (root_admin.username(), 'planted'))
        names = [item['username'] for item in member_store.list_accounts()]
        assert root_admin.username() not in names

    def test_not_a_row_in_member_accounts(self):
        root_admin.create_if_missing('root-secret-123')
        from server import db
        with db.read() as conn:
            row = conn.execute('SELECT username FROM member_accounts WHERE username=?',
                               (root_admin.username(),)).fetchone()
        assert row is None

    def test_survives_a_database_reset(self):
        root_admin.create_if_missing('root-secret-123')
        from server import db
        with db.write() as conn:
            conn.execute('DELETE FROM member_accounts')
        assert root_admin.exists() is True
        assert root_admin.verify('root-secret-123') is True
        assert permissions.is_admin(root_admin.username()) is True

    def test_session_version_changes_with_the_password(self):
        root_admin.create_if_missing('root-secret-123')
        before = root_admin.session_version()
        root_admin.change_password('another-password')
        assert root_admin.session_version() != before

    def test_unauthenticated_verify_is_false(self):
        assert root_admin.verify('anything') is False
        assert root_admin.exists() is False
        with self.assertRaises(LookupError):
            root_admin.change_password('another-password')

    def test_legacy_admin_cache_does_not_hide_the_root(self):
        root_admin.create_if_missing('root-secret-123')
        # admin_store.usernames() is the legacy list; the root is intentionally absent.
        assert root_admin.username() not in admin_store.usernames()

    def test_bootstrap_existing_creates_once_and_prints_once(self):
        from server import root_admin as ra
        password = ra.bootstrap_existing()
        assert password and ra.exists() is True
        assert ra.verify(password) is True
        # The generated password is handed out only once.
        assert ra.take_generated() == password
        assert ra.take_generated() is None
        # A second boot leaves the account alone.
        assert ra.bootstrap_existing() is None
        assert ra.verify(password) is True

    def test_bootstrap_does_not_touch_existing_accounts(self):
        from server import root_admin as ra
        member_store.create('张三')
        password = ra.bootstrap_existing()
        assert ra.verify(password) is True
        assert member_store.verify('张三', member_store.INITIAL_PASSWORD) is True

    def test_storage_layer_refuses_to_weaken_root(self):
        root_admin.create_if_missing('root-secret-123')
        with db.write() as conn:
            conn.execute("INSERT INTO member_accounts(username,password,active,permissions)"
                         " VALUES(?,?,1,'user')", (root_admin.username(), 'planted'))
        for call in (lambda: member_store.update(root_admin.username(), active=False),
                     lambda: member_store.delete(root_admin.username()),
                     lambda: member_store.set_password(root_admin.username(), 'hacked123'),
                     lambda: member_store.change_password(root_admin.username(), 'x', 'hacked123'),
                     lambda: permissions.set_permissions(root_admin.username(), [])):
            with self.assertRaises(ValueError):
                call()
        # Session helpers follow the file, so a planted row cannot mint a duty session.
        assert member_store.authenticate(root_admin.username(), 'planted') is None
        assert member_store.session_valid(root_admin.username(), 'planted-digest') is False
        assert root_admin.verify('root-secret-123') is True

    def test_root_never_gets_a_row_in_member_accounts(self):
        # 原始管理员只存在于 data/root_admin.json；账号表里绝不会有这一行。
        root_admin.create_if_missing('root-secret-123')
        with db.write() as conn:
            # Even a stray mirror row is purged, and permissions stay fixed regardless.
            conn.execute("INSERT INTO member_accounts(username,password,active,permissions)"
                         " VALUES(?,?,1,'user')", (root_admin.username(), 'whatever'))
        assert permissions.permissions_of(root_admin.username()) == set(permissions.ALL_PERMISSIONS)
        root_admin.purge_mirror_row()
        with db.read() as conn:
            row = conn.execute('SELECT username FROM member_accounts WHERE username=?',
                               (root_admin.username(),)).fetchone()
        assert row is None, '原始管理员不应出现在账号表中'
        assert root_admin.verify('root-secret-123') is True
