import unittest

import test_schedule
from server import data_admin, db, schedule_store


class TestDataAdmin(test_schedule.ScheduleTest):
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

    def test_lists_only_whitelisted_tables(self):
        names = {item['name'] for item in data_admin.list_tables()['items']}
        assert 'shifts' in names and 'field_duty' in names
        assert names <= set(data_admin.TABLES)

    def test_unknown_table_is_rejected(self):
        for name in ('sqlite_master', 'shifts; DROP TABLE shifts', 'not_a_table'):
            with self.assertRaises(LookupError):
                data_admin.preview(name)

    def test_preview_pages_and_reports_columns(self):
        for index in range(3):
            schedule_store.save(dict(day='2026-11-0%d' % (index + 1), start='09:00',
                                     end='10:00', members=['甲']))
        page = data_admin.preview('shifts', page=1, size=2)
        assert page['total'] == 3 and page['pages'] == 2 and len(page['items']) == 2
        columns = {item['name']: item for item in page['columns']}
        assert columns['note']['editable'] is True
        assert columns['id']['editable'] is False
        second = data_admin.preview('shifts', page=2, size=2)
        assert len(second['items']) == 1

    def test_search_filters_rows(self):
        schedule_store.save(dict(day='2026-11-02', start='09:00', end='10:00', members=['甲']))
        schedule_store.save(dict(day='2026-11-03', start='09:00', end='10:00', members=['乙']))
        page = data_admin.preview('shifts', q='2026-11-03')
        assert page['total'] == 1 and page['items'][0]['day'] == '2026-11-03'

    def test_passwords_are_masked_and_not_writable(self):
        with db.write() as conn:
            conn.execute("INSERT INTO member_accounts(username,password,active,permissions)"
                         " VALUES('张三','scrypt$hash',1,'user')")
        page = data_admin.preview('member_accounts', q='张三')
        row = page['items'][0]
        assert row['password'] == '••••••'
        secret = {item['name']: item['secret'] for item in page['columns']}
        assert secret['password'] is True
        with self.assertRaises(ValueError):
            data_admin.update_row('member_accounts', {'username': '张三'},
                                  {'password': 'plain-text'})

    def test_secret_settings_values_are_masked(self):
        with db.write() as conn:
            conn.execute("INSERT INTO settings(key,value) VALUES('mcp','{\"key\":\"secret\"}')")
            conn.execute("INSERT INTO settings(key,value) VALUES('path','abc123')")
        page = data_admin.preview('settings')
        values = {row['key']: row['value'] for row in page['items']}
        assert values['mcp'] == '••••••'
        assert values['path'] == 'abc123'

    def test_update_whitelisted_column_only(self):
        sid = schedule_store.save(dict(day='2026-11-02', start='09:00', end='10:00', members=['甲']))
        result = data_admin.update_row('shifts', {'id': sid}, {'note': '临时调整'})
        assert result['columns'] == ['note'] and result['updated'] == 1
        row = data_admin.preview('shifts', q='临时调整')['items'][0]
        assert row['note'] == '临时调整'
        with self.assertRaises(ValueError):
            data_admin.update_row('shifts', {'id': sid}, {'id': 999})

    def test_update_rejects_missing_key(self):
        schedule_store.save(dict(day='2026-11-02', start='09:00', end='10:00', members=['甲']))
        with self.assertRaises(ValueError):
            data_admin.update_row('shifts', {}, {'note': 'x'})
        with self.assertRaises(LookupError):
            data_admin.update_row('shifts', {'id': 9999}, {'note': 'x'})

    def test_audit_trail_refuses_edits_and_deletes(self):
        with db.write() as conn:
            conn.execute("INSERT INTO operation_log(at,actor,role,action,detail)"
                         " VALUES('2026-11-02T09:00:00+08:00','root','admin','测试','记录')")
        with self.assertRaises(ValueError):
            data_admin.update_row('operation_log', {'id': 1}, {'detail': '改'})
        rows = data_admin.preview('operation_log')['items']
        # The data console cannot erase the evidence of its own use, even with `data`.
        assert data_admin.TABLES['operation_log']['deletable'] is False
        with self.assertRaises(ValueError):
            data_admin.delete_row('operation_log', {'id': rows[0]['id']})
        with self.assertRaises(ValueError):
            data_admin.delete_many('operation_log', [{'id': rows[0]['id']}])
        with self.assertRaises(ValueError):
            data_admin.delete_page('operation_log')
        assert data_admin.preview('operation_log')['total'] == 1

    def test_delete_removes_exactly_one_row(self):
        sid = schedule_store.save(dict(day='2026-11-02', start='09:00', end='10:00', members=['甲']))
        schedule_store.save(dict(day='2026-11-03', start='09:00', end='10:00', members=['乙']))
        assert data_admin.delete_row('shifts', {'id': sid})['deleted'] == 1
        assert data_admin.preview('shifts')['total'] == 1

    def test_cannot_delete_your_own_account_here(self):
        with db.write() as conn:
            conn.execute("INSERT INTO member_accounts(username,password,active,permissions)"
                         " VALUES('root','h',1,'user')")
        with self.assertRaises(ValueError):
            data_admin.delete_row('member_accounts', {'username': 'root'}, actor='root')

    def test_composite_primary_key_is_required(self):
        sid = schedule_store.save(dict(day='2026-11-02', start='09:00', end='10:00', members=['甲']))
        with self.assertRaises(ValueError):
            data_admin.delete_row('attendance', {'shift_id': sid})
        assert data_admin.delete_row('attendance', {'shift_id': sid, 'name': '甲'})['deleted'] == 1


class TestDataAdminBatch(test_schedule.ScheduleTest):
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

    def test_attendance_preview_includes_shift_timestamps(self):
        sid = schedule_store.save(dict(day='2026-11-02', start='09:00', end='11:00', members=['甲']))
        page = data_admin.preview('attendance', q='甲')
        row = page['items'][0]
        assert row['shift_id'] == sid
        assert row['班次.day'] == '2026-11-02'
        assert row['班次.start'] == '09:00'
        assert row['班次.end'] == '11:00'
        names = [column['name'] for column in page['columns']]
        assert names[:3] == ['shift_id', 'name', 'checked_at']
        assert '班次.day' in names and '班次.start' in names
        read_only = {column['name']: column['editable'] for column in page['columns']}
        assert read_only['班次.day'] is False

    def test_joined_columns_survive_search(self):
        schedule_store.save(dict(day='2026-11-02', start='09:00', end='11:00', members=['甲']))
        schedule_store.save(dict(day='2026-11-03', start='09:00', end='11:00', members=['乙']))
        page = data_admin.preview('attendance', q='乙')
        assert page['total'] == 1
        assert page['items'][0]['班次.day'] == '2026-11-03'

    def test_delete_many_removes_selected_rows_only(self):
        a = schedule_store.save(dict(day='2026-11-02', start='09:00', end='10:00', members=['甲']))
        b = schedule_store.save(dict(day='2026-11-03', start='09:00', end='10:00', members=['乙']))
        c = schedule_store.save(dict(day='2026-11-04', start='09:00', end='10:00', members=['丙']))
        result = data_admin.delete_many('shifts', [{'id': a}, {'id': c}])
        assert result == {'table': 'shifts', 'deleted': 2, 'requested': 2}
        remaining = [row['id'] for row in data_admin.preview('shifts')['items']]
        assert remaining == [b]

    def test_delete_many_skips_malformed_keys(self):
        sid = schedule_store.save(dict(day='2026-11-02', start='09:00', end='10:00', members=['甲']))
        result = data_admin.delete_many('shifts', [{'id': sid}, {}, {'nope': 1}])
        assert result['deleted'] == 1 and result['requested'] == 3

    def test_delete_many_rejects_an_empty_selection(self):
        with self.assertRaises(ValueError):
            data_admin.delete_many('shifts', [])

    def test_delete_page_clears_exactly_the_visible_page(self):
        for index in range(5):
            schedule_store.save(dict(day='2026-11-0%d' % (index + 1), start='09:00',
                                     end='10:00', members=['甲']))
        result = data_admin.delete_page('shifts', page=1, size=2)
        assert result['deleted'] == 2 and result['requested'] == 2
        assert data_admin.preview('shifts')['total'] == 3
        # The second page now holds what used to be rows 3-5.
        assert [row['day'] for row in data_admin.preview('shifts', page=1, size=2)['items']] == \
            ['2026-11-03', '2026-11-04']

    def test_delete_page_respects_the_search_filter(self):
        schedule_store.save(dict(day='2026-11-02', start='09:00', end='10:00', members=['甲']))
        schedule_store.save(dict(day='2026-11-03', start='09:00', end='10:00', members=['乙']))
        result = data_admin.delete_page('shifts', page=1, size=50, q='2026-11-03')
        assert result['deleted'] == 1
        assert [row['day'] for row in data_admin.preview('shifts')['items']] == ['2026-11-02']

    def test_delete_page_on_an_empty_table_is_safe(self):
        result = data_admin.delete_page('shifts')
        assert result['deleted'] == 0 and result['requested'] == 0

    def test_batch_delete_never_touches_the_root_account(self):
        from server import root_admin
        root_admin.create_if_missing('root-secret-123')
        data_admin.delete_page('member_accounts')
        assert root_admin.verify('root-secret-123') is True


class TestDataAdminProtection(test_schedule.ScheduleTest):
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

    def test_settings_rows_cannot_be_deleted(self):
        with db.write() as conn:
            conn.execute("INSERT INTO settings(key,value) VALUES('path','\"abc12345\"')")
        page = data_admin.preview('settings')
        assert page['deletable'] is False
        assert all(item['deletable'] is False for item in page['items'])
        with self.assertRaises(ValueError):
            data_admin.delete_row('settings', {'key': 'path'})
        with self.assertRaises(ValueError):
            data_admin.delete_many('settings', [{'key': 'path'}])
        with self.assertRaises(ValueError):
            data_admin.delete_page('settings')
        assert data_admin.preview('settings')['total'] == 1, '设置行必须还在'

    def test_settings_values_remain_editable(self):
        with db.write() as conn:
            conn.execute("INSERT INTO settings(key,value) VALUES('chat_max_mb','32')")
        data_admin.update_row('settings', {'key': 'chat_max_mb'}, {'value': '64'})
        rows = {row['key']: row['value'] for row in data_admin.preview('settings')['items']}
        assert rows['chat_max_mb'] == '64'

    def test_login_affecting_settings_cannot_be_rewritten_here(self):
        """The bootstrap flag is a database row; it must not re-open registration."""
        with db.write() as conn:
            conn.execute("INSERT INTO settings(key,value) VALUES('setup_done','\"alice\"')")
            conn.execute("INSERT INTO settings(key,value) VALUES('path','\"abc12345\"')")
        for key in ('setup_done', 'path', 'session_idle', 'duty_rules', 'system_clock'):
            with self.assertRaises(ValueError, msg=key):
                data_admin.update_row('settings', {'key': key}, {'value': '"x"'})

    def test_table_list_reports_deletability(self):
        tables = {item['name']: item for item in data_admin.list_tables()['items']}
        assert tables['settings']['deletable'] is False
        assert tables['shifts']['deletable'] is True

    def test_legacy_admin_table_is_labelled_as_root_and_read_only(self):
        assert data_admin.TABLES['users']['label'].startswith('原始管理员')
        assert data_admin.TABLES['users']['deletable'] is False
        with db.write() as conn:
            conn.execute("INSERT INTO users(username,password) VALUES('oldroot','hash')")
        page = data_admin.preview('users')
        assert page['deletable'] is False
        assert all(item['deletable'] is False for item in page['items'])
        with self.assertRaises(ValueError):
            data_admin.delete_page('users')

    def test_root_row_is_marked_not_deletable(self):
        from server import root_admin
        root_admin.create_if_missing('root-secret-123')
        with db.write() as conn:
            conn.execute("INSERT INTO member_accounts(username,password,active,permissions)"
                         " VALUES('张三','h',1,'user')")
        page = data_admin.preview('member_accounts')
        by_name = {row['username']: row for row in page['items']}
        assert '原始管理员' not in by_name, '原始管理员不应出现在账号预览里'
        assert by_name['张三']['deletable'] is True
