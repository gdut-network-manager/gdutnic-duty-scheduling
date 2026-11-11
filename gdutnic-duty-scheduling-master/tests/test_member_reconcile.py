import unittest
from fastapi.testclient import TestClient
import test_schedule
from server import admin_store, db, member_store, permissions, schedule_store, weekly_store


class MemberReconcileTest(unittest.TestCase):
    setUp = test_schedule.ScheduleTest.setUp
    tearDown = test_schedule.ScheduleTest.tearDown

    def roster(self):
        roster = weekly_store.empty_roster()
        roster['slots'][0]['days'][0] = ['每周人员']
        weekly_store.save(roster)
        schedule_store.save(dict(day='2026-11-09', start='08:30', end='10:15',
                                 members=['特殊人员'], special=True))

    def test_provision_is_repeatable_and_preserves_passwords(self):
        self.roster()
        member_store.create('每周人员', 'changed-password')
        result = member_store.provision()
        self.assertEqual(result, {'created': 1, 'existing': 1})
        self.assertTrue(member_store.verify('每周人员', 'changed-password'))
        self.assertTrue(member_store.verify('特殊人员', 'gg2017'))
        self.assertEqual(member_store.provision()['created'], 0)

    def test_cleanup_removes_only_unused_default_accounts_and_invalidates_sessions(self):
        self.roster()
        member_store.provision()
        for name in ['多余人员', '其他组人员']:
            member_store.create(name)
        group = permissions.save_group('其他组', ['duty'])
        permissions.set_group('其他组人员', group['id'])
        version = member_store.session_version('多余人员')
        self.assertTrue(member_store.session_valid('多余人员', version))
        with db.read() as conn:
            attendance = conn.execute('SELECT COUNT(*) AS n FROM attendance').fetchone()['n']
        self.assertEqual(member_store.cleanup_unused(), {'deleted': 1, 'usernames': ['多余人员']})
        self.assertFalse(member_store.session_valid('多余人员', version))
        self.assertEqual({r['username'] for r in member_store.list_accounts()},
                         {'每周人员', '特殊人员', '其他组人员'})
        self.assertEqual(member_store.provision()['created'], 0)
        self.assertEqual(member_store.cleanup_unused()['deleted'], 0)
        with db.read() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) AS n FROM attendance').fetchone()['n'], attendance)

    def test_api_permissions_and_reconciliation(self):
        from server.app import app
        self.roster()
        client = TestClient(app)
        for route in ['provision', 'cleanup']:
            self.assertEqual(client.post('/api/admin/members/' + route).status_code, 401)
        member_store.create('排班管理员', 'password123')
        permissions.set_permissions('排班管理员', ['weekly'])
        admin_store.remember_setup('排班管理员')
        client.get('/' + admin_store.path())
        response = client.post('/api/admin/login', json={'username': '排班管理员', 'password': 'password123'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(client.post('/api/admin/members/provision').status_code, 200)
        self.assertEqual(client.post('/api/admin/members/cleanup').status_code, 403)
        permissions.set_permissions('排班管理员', ['members'])
        member_store.create('待清理')
        response = client.post('/api/admin/members/cleanup')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('待清理', response.json()['usernames'])
