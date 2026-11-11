"""Real login cookies, deterministic idle time, and background request checks."""
from unittest.mock import patch
from fastapi.testclient import TestClient
from test_permissions import PermissionFixture
from server import app as api, admin_store, member_store, permissions, session_policy, db


class SessionTimeoutTest(PermissionFixture):
    def setUp(self):
        super().setUp()
        member_store.create('alice', 'password123')
        permissions.set_permissions('alice', permissions.ALL_PERMISSIONS)
        admin_store.remember_setup('alice')
        self.clock = patch.object(session_policy.time, 'time', return_value=2000000000.0)
        self.time = self.clock.start()
        self.addCleanup(self.clock.stop)
        self.member = TestClient(api.app)
        self.admin = TestClient(api.app)
        self.admin.get('/' + admin_store.path())
        self.assertEqual(self.member.post('/api/member/login', json={'username': 'alice', 'password': 'password123'}).status_code, 200)
        self.assertEqual(self.admin.post('/api/admin/login', json={'username': 'alice', 'password': 'password123'}).status_code, 200)
        self.addCleanup(self.member.close)
        self.addCleanup(self.admin.close)

    def advance(self, seconds):
        # Keep devices responsive while exercising the independent idle policy.
        target = self.time.return_value + seconds
        while self.time.return_value < target:
            self.time.return_value = min(target, self.time.return_value + 20)
            with db.read() as conn:
                sessions = [dict(row) for row in conn.execute('SELECT id,kind,username FROM login_sessions')]
            for record in sessions:
                session_policy.heartbeat(record['id'], record['kind'], record['username'])

    def test_default_limits_and_admin_member_expire_independently(self):
        self.assertEqual(session_policy.settings(), {'member_minutes': 120, 'admin_minutes': 30, 'member_response_seconds':90, 'admin_response_seconds':90})
        self.advance(1800)
        self.assertEqual(self.admin.get('/api/admin/rules').status_code, 401)
        self.assertTrue(self.member.get('/api/member/status').json()['logged_in'])
        self.advance(5400)
        self.assertFalse(self.member.get('/api/member/status').json()['logged_in'])
        self.assertEqual(self.member.get('/api/member/field-duty').status_code, 401)

    def test_background_requests_do_not_extend_idle_deadline(self):
        initial = self.admin.get('/api/session/idle?role=admin').json()['expires_at']
        for _ in range(5):
            self.advance(300)
            self.assertEqual(self.admin.get('/api/admin/rules').status_code, 200)
            state = self.admin.get('/api/session/idle?role=admin').json()
            self.assertEqual(state['expires_at'], initial)
        self.advance(300)
        self.assertFalse(self.admin.get('/api/session/idle?role=admin').json()['logged_in'])

    def test_real_activity_extends_deadline_but_cannot_revive_expired_login(self):
        self.advance(1790)
        self.assertEqual(self.admin.post('/api/session/activity?role=admin', json={}).status_code, 200)
        self.advance(1790)
        self.assertEqual(self.admin.get('/api/admin/rules').status_code, 200)
        self.advance(10)
        self.assertEqual(self.admin.post('/api/session/activity?role=admin', json={}).status_code, 401)
        self.assertEqual(self.admin.get('/api/admin/rules').status_code, 401)

    def test_logout_revokes_copied_cookies_on_server(self):
        copy = TestClient(api.app)
        copy.cookies.update(self.admin.cookies)
        self.addCleanup(copy.close)
        self.assertEqual(self.admin.post('/api/admin/logout').status_code, 200)
        self.assertEqual(copy.get('/api/admin/rules').status_code, 401)

    def test_admin_cookie_alone_is_also_limited(self):
        self.admin.cookies.delete('gdutnic_wiki')
        self.assertEqual(self.admin.get('/api/admin/rules').status_code, 200)
        self.advance(1800)
        self.assertEqual(self.admin.get('/api/admin/rules').status_code, 401)

    def test_settings_require_rules_permission_and_validate_minutes(self):
        for body in ({'member_minutes': 0, 'admin_minutes': 30}, {'member_minutes': 120, 'admin_minutes': True}, {'member_minutes': 120, 'admin_minutes': 241}):
            self.assertEqual(self.admin.put('/api/admin/session-timeout', json=body).status_code, 400)
        self.assertEqual(self.admin.put('/api/admin/session-timeout', json={'member_minutes': 60, 'admin_minutes': 10}).status_code, 200)
        self.assertEqual(self.admin.get('/api/admin/session-timeout').json(), {'member_minutes': 60, 'admin_minutes': 10, 'member_response_seconds':90, 'admin_response_seconds':90})
        self.advance(600)
        self.assertEqual(self.admin.get('/api/admin/rules').status_code, 401)
        self.assertTrue(self.member.get('/api/member/status').json()['logged_in'])

    def test_same_browser_tabs_share_activity_but_other_logins_do_not(self):
        same = TestClient(api.app)
        same.cookies.update(self.member.cookies)
        self.addCleanup(same.close)
        other = TestClient(api.app)
        self.assertEqual(other.post('/api/member/login', json={'username': 'alice', 'password': 'password123'}).status_code, 200)
        self.addCleanup(other.close)
        self.advance(7100)
        self.assertEqual(same.post('/api/session/activity', json={}).status_code, 200)
        self.advance(100)
        self.assertTrue(self.member.get('/api/member/status').json()['logged_in'])
        self.assertFalse(other.get('/api/member/status').json()['logged_in'])

    def test_timestamp_cannot_set_future_deadline_and_old_heartbeats_do_not_extend(self):
        initial = self.member.get('/api/session/idle').json()['expires_at']
        self.advance(100)
        self.assertEqual(self.member.post('/api/session/activity', json={'activity_at': 2000000000}).status_code, 200)
        self.assertEqual(self.member.get('/api/session/idle').json()['expires_at'], initial)
        self.assertEqual(self.member.post('/api/session/activity', json={'activity_at': self.time.return_value + 9999999}).status_code, 200)
        self.assertEqual(self.member.get('/api/session/idle').json()['expires_at'], self.time.return_value + 7200)

    def test_legacy_leases_without_login_method_are_revoked_on_upgrade(self):
        from server import db
        with db.write() as conn:
            conn.execute('ALTER TABLE login_sessions DROP COLUMN sso_identity')
        db._conn.close()
        db._conn = None
        with db.read() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) AS n FROM login_sessions').fetchone()['n'], 0)
        self.assertEqual(self.admin.get('/api/admin/rules').status_code, 401)


    def test_global_heartbeat_revokes_only_the_unresponsive_device(self):
        initial = self.member.get('/api/session/idle').json()['expires_at']
        for _ in range(3):
            self.time.return_value += 30
            self.assertEqual(self.member.post('/api/session/heartbeat').status_code, 200)
        self.assertEqual(self.member.get('/api/session/idle').json()['expires_at'], initial)
        # Administrator device never replied, even though both use the same username.
        self.assertEqual(session_policy.reap_unresponsive(), 1)
        self.assertEqual(self.admin.get('/api/admin/rules').status_code, 401)
        self.assertTrue(self.member.get('/api/member/status').json()['logged_in'])
        self.time.return_value += 90
        self.assertEqual(self.member.post('/api/session/heartbeat').status_code, 401)
        self.assertFalse(self.member.get('/api/member/status').json()['logged_in'])

    def test_presence_limits_can_differ_between_member_and_admin(self):
        body = {'member_minutes':120, 'admin_minutes':30, 'member_response_seconds':30, 'admin_response_seconds':120}
        self.assertEqual(self.admin.put('/api/admin/session-timeout',json=body).status_code,200)
        self.assertEqual(self.admin.get('/api/admin/session-timeout').json(),body)
        self.time.return_value += 30
        self.assertEqual(self.member.post('/api/session/heartbeat').status_code,401)
        response = self.admin.post('/api/session/heartbeat?role=admin')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['timeout_seconds'],120)
        self.assertNotIn('set-cookie',response.headers)
        self.assertEqual(self.admin.post('/api/session/heartbeat?role=invalid').status_code,400)
        for value in [0,29,3601,True,'90']:
            invalid = {**body,'admin_response_seconds':value}
            self.assertEqual(self.admin.put('/api/admin/session-timeout',json=invalid).status_code,400)
