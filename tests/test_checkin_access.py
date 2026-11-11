"""Network matching, persistence, API enforcement and management permissions."""
from unittest.mock import patch
from fastapi.testclient import TestClient
from test_permissions import PermissionFixture
from server import app as api, db, admin_store, checkin_access as access, member_store, permissions, schedule_store


def rules(checkin='', field='', mode='allow'):
    return {'checkin': {'mode': mode, 'entries': checkin},
            'field': {'mode': mode, 'entries': field}}


class CheckinAccessTest(PermissionFixture):
    def login(self, host='192.168.1.20', admin=False):
        client = TestClient(api.app, client=(host, 43210))
        if admin:
            client.get('/' + admin_store.path())
        response = client.post('/api/admin/login' if admin else '/api/member/login',
                               json={'username': 'alice', 'password': 'password123'})
        self.assertEqual(response.status_code, 200, response.text)
        return client

    def setUp(self):
        super().setUp()
        member_store.create('alice', 'password123')

    def test_networks_masks_localhost_and_mapped_ipv4(self):
        value = access.normalize(rules('192.168.1.9/255.255.255.0\nlocalhost\n2001:db8::/32'))['checkin']
        for host in ['192.168.1.20', '127.0.0.2', '::1', '::ffff:192.168.1.20', '2001:db8::123']:
            self.assertTrue(access.allowed(value, host), host)
        for host in ['192.168.2.1', '2001:db9::1', 'unknown', 'localhost']:
            self.assertFalse(access.allowed(value, host), host)
        self.assertTrue(access.allowed({'mode':'allow', 'entries':'10.0.0.1/32'}, '10.0.0.1'))
        self.assertFalse(access.allowed({'mode':'allow', 'entries':'10.0.0.1/32'}, '10.0.0.2'))

    def test_empty_and_exclusion_semantics(self):
        self.assertTrue(access.status('192.168.1.20')['checkin'])
        self.assertFalse(access.allowed({'mode':'allow', 'entries':''}, '192.168.1.20'))
        exclude = {'mode':'exclude', 'entries':'192.168.1.0/24'}
        self.assertFalse(access.allowed(exclude, '192.168.1.20'))
        self.assertTrue(access.allowed(exclude, '10.0.0.1'))

    def test_invalid_save_does_not_replace_existing_rules(self):
        saved = access.save(rules('localhost', '10.0.0.0/8'))
        for body in [None, {}, rules('hostname.invalid'), rules('10.0.0.0/99'), rules(mode='bogus')]:
            with self.assertRaises(ValueError):
                access.save(body)
            self.assertEqual(access.load(), saved)

    def test_network_rule_is_independent_for_each_action_and_enforced(self):
        access.save(rules('10.0.0.0/8', '192.168.1.0/24'))
        client = self.login()
        self.assertFalse(client.get('/api/member/status').json()['network_access']['checkin'])
        # IP restriction runs before any attendance mutation, even if the caller crafts a request.
        with patch.object(schedule_store, 'check_in_with_chain') as check:
            self.assertEqual(client.post('/api/schedules/123/check-in').status_code, 403)
            check.assert_not_called()
        self.assertEqual(client.post('/api/member/field-duty').status_code, 200)
        access.save(rules('192.168.1.0/24', '10.0.0.0/8'))
        self.assertEqual(client.post('/api/member/field-duty').status_code, 403)
        with patch.object(schedule_store, 'check_in_with_chain', return_value=('stamp', [])) as check:
            self.assertEqual(client.post('/api/schedules/123/check-in').status_code, 200)
            check.assert_called_once_with(123, 'alice')

    def test_forwarded_headers_do_not_override_peer(self):
        access.save(rules('10.0.0.0/8', '10.0.0.0/8'))
        response = self.login().get('/api/network-access', headers={'X-Forwarded-For':'10.0.0.1', 'X-Real-IP':'10.0.0.1'})
        self.assertEqual(response.json(), {'ip':'192.168.1.20', 'checkin':False, 'field':False})

    def test_administration_requires_dedicated_permission(self):
        admin_store.remember_setup('alice')
        permissions.set_permissions('alice', ['logs'])
        client = self.login(admin=True)
        self.assertEqual(client.get('/api/admin/checkin-access').status_code, 403)
        self.assertEqual(client.put('/api/admin/checkin-access', json=rules()).status_code, 403)
        permissions.set_permissions('alice', ['checkin_manage'])
        self.assertEqual(client.get('/api/admin/checkin-access').status_code, 200)
        response = client.put('/api/admin/checkin-access', json=rules('192.168.1.0/24', 'localhost'))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['network_access']['checkin'])
        self.assertFalse(response.json()['network_access']['field'])
        self.assertEqual(client.put('/api/admin/checkin-access', json=rules('invalid')).status_code, 400)

    def test_member_schedule_retains_member_rules_with_an_admin_session_in_same_browser(self):
        from server import duty_settings, system_clock
        from datetime import datetime
        admin_store.remember_setup('alice')
        permissions.set_permissions('alice', ['duty', 'checkin_manage'])
        client = self.login()
        client.get('/' + admin_store.path())
        self.assertEqual(client.post('/api/admin/login', json={'username':'alice', 'password':'password123'}).status_code, 200)
        sid = schedule_store.save(dict(day='2026-11-10', start='08:30', end='10:15', members=['alice']))
        schedule_store.save(dict(day='2026-11-10', start='10:15', end='12:00', members=['bob']))
        duty_settings.save({'early_minutes':15, 'late_minutes':10, 'absent_minutes':30})
        with patch.object(system_clock, 'now', return_value=datetime(2026,11,10,8,18,tzinfo=schedule_store.TZ)):
            response = client.get('/api/member/schedules?day=2026-11-10')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual([item['id'] for item in response.json()['items']], [sid])
            self.assertEqual(response.json()['rules']['early_minutes'], 15)
            state = client.get('/api/member/status')
            self.assertEqual(state.json()['rules']['early_minutes'],15)
            self.assertIn('T08:18:00', state.json()['now']['current'])
            self.assertEqual(state.headers['Cache-Control'], 'no-store')
            self.assertEqual(client.post(f'/api/schedules/{sid}/check-in').status_code, 200)

    def test_overview_reads_remain_visible_without_action_permissions(self):
        permissions.set_permissions('alice', [])
        client = self.login()
        sid = schedule_store.save(dict(day='2026-11-10', start='08:30', end='10:15', members=['alice']))
        schedule_store.save(dict(day='2026-11-10', start='10:15', end='12:00', members=['bob']))
        response = client.get('/api/member/schedules?day=2026-11-10')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['id'] for item in response.json()['items']], [sid])
        self.assertEqual(client.get('/api/member/field-duty').status_code, 200)
        self.assertEqual(client.post(f'/api/schedules/{sid}/check-in').status_code, 403)
        self.assertEqual(client.post('/api/member/field-duty').status_code, 403)

    def test_member_overview_permission_tree_has_two_action_leaves(self):
        overview = permissions.TREE[0]['children'][0]
        self.assertEqual(overview['label'], '概览')
        self.assertEqual([(node['id'], node['label']) for node in overview['children']],
                         [('duty', '打卡'), ('member_field', '外勤')])
        self.assertEqual(set(permissions.leaves()), set(permissions.GRANTABLE))

    def test_merged_settings_preserves_independent_endpoint_permissions(self):
        admin_store.remember_setup('alice')
        for granted, own, other in [(['sso'], '/api/admin/sso', '/api/admin/checkin-access'),
                                    (['checkin_manage'], '/api/admin/checkin-access', '/api/admin/sso')]:
            permissions.set_permissions('alice', granted)
            client = self.login(admin=True)
            self.assertIn('settings', client.get('/api/admin/status').json()['permissions'])
            self.assertEqual(client.get(own).status_code, 200)
            self.assertEqual(client.get(other).status_code, 403)
            self.assertEqual(client.get('/api/admin/model').status_code, 403)
            self.assertEqual(client.get('/api/admin/mcp').status_code, 403)

    def test_member_cards_and_timeline_receive_coworker_attendance(self):
        from datetime import datetime
        from server import system_clock
        client = self.login()
        sid = schedule_store.save(dict(day='2026-11-10', start='08:30', end='10:15', members=['alice','bob']))
        schedule_store.save(dict(day='2026-11-10', start='10:15', end='12:00', members=['someone-else']))
        with patch.object(system_clock, 'now', return_value=datetime(2026,11,10,8,35,tzinfo=schedule_store.TZ)):
            schedule_store.check_in(sid, 'bob')
            response = client.get('/api/member/schedules?day=2026-11-10')
            self.assertEqual(response.status_code, 200)
            self.assertEqual([item['id'] for item in response.json()['items']], [sid])
            members = {item['name']: item for item in response.json()['items'][0]['members']}
            self.assertEqual(set(members), {'alice','bob'})
            self.assertEqual(members['alice']['status'], 'pending')
            self.assertEqual(members['bob']['status'], 'normal')
            self.assertIsNotNone(members['bob']['checked_at'])
            self.assertEqual(client.post(f'/api/schedules/{sid}/check-in',json={'username':'bob'}).status_code, 200)
            with db.read() as conn:
                self.assertIsNotNone(conn.execute('SELECT checked_at FROM attendance WHERE shift_id=? AND name=?', (sid,'alice')).fetchone()['checked_at'])
