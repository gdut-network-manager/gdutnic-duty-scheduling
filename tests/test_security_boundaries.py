"""Security boundary checks using real login sessions and a temporary SQLite DB."""
import re
from unittest.mock import patch

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from test_permissions import PermissionFixture
from server import app as api, admin_store, db, field_duty, member_store, permissions, schedule_store, security, weekly_store


class SecurityBoundariesTest(PermissionFixture):
    def setUp(self):
        super().setUp()
        self.patches = [patch.object(api, 'ADMIN_LIMITER', security.AttemptLimiter()),
                        patch.object(api, 'ACCOUNT_LIMITER', security.AttemptLimiter()),
                        patch.object(weekly_store, 'SEED', db.DATA / 'weekly_roster.json')]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        member_store.create('alice', 'password123')
        member_store.create('bob', 'password123')
        admin_store.remember_setup('alice')

    def login(self, name='alice', admin=False):
        client = TestClient(api.app)
        if admin:
            client.get('/' + admin_store.path())
        response = client.post('/api/admin/login' if admin else '/api/member/login',
                               json={'username': name, 'password': 'password123'})
        self.assertEqual(response.status_code, 200, response.text)
        return client

    def test_all_privileged_admin_routes_reject_anonymous_and_member_sessions(self):
        clients = [TestClient(api.app), self.login()]
        exempt = {'/api/admin/status', '/api/admin/login', '/api/admin/register', '/api/admin/logout'}
        checked = 0
        for route in api.app.routes:
            if not isinstance(route, APIRoute) or not route.path.startswith('/api/admin/') or route.path in exempt:
                continue
            path = re.sub(r'\{([^}]+)\}', lambda m: '1' if m[1] in ('shift_id', 'swap_id') else 'probe', route.path)
            for method in route.methods:
                for client in clients:
                    response = client.request(method, path, params={'day': '2026-11-02'}, json={})
                    self.assertEqual(response.status_code, 401, (method, path, response.text))
                checked += 1
        self.assertGreaterEqual(checked, 65)
        print(f'Validated {checked} privileged admin operations for anonymous and member sessions')

    def test_member_cannot_gain_admin_by_visiting_secret_slug(self):
        client = self.login()
        client.get('/' + admin_store.path())
        response = client.post('/api/admin/login', json={'username': 'alice', 'password': 'password123'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(client.get('/api/admin/data/tables').status_code, 401)

    def test_admin_password_reset_revokes_old_admin_session(self):
        permissions.set_permissions('alice', ['logs'])
        client = self.login(admin=True)
        self.assertEqual(client.get('/api/admin/logs').status_code, 200)
        member_store.set_password('alice', 'new-password-456')
        self.assertEqual(client.get('/api/admin/logs').status_code, 401)

    def test_field_confirmation_permission_and_revocation(self):
        client = self.login()
        field_duty.register('bob')
        self.assertEqual(client.post('/api/member/field-duty/pending/bob').status_code, 200)
        self.assertEqual(field_duty.member_status('bob')['status'], field_duty.CONFIRMED)
        permissions.set_permissions('alice', ['duty', 'member_field'])
        for method in ('POST', 'DELETE'):
            self.assertEqual(client.request(method, '/api/member/field-duty/pending/bob').status_code, 403)
        self.assertEqual(field_duty.member_status('bob')['status'], field_duty.CONFIRMED)

    def test_pending_reject_cannot_delete_confirmed_record(self):
        client = self.login()
        field_duty.register('bob')
        field_duty.confirm('bob')
        response = client.delete('/api/member/field-duty/pending/bob')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(field_duty.member_status('bob')['status'], field_duty.CONFIRMED)

    def test_pending_reject_removes_only_pending_record(self):
        client = self.login()
        field_duty.register('bob')
        self.assertEqual(client.delete('/api/member/field-duty/pending/bob').status_code, 200)
        self.assertEqual(field_duty.member_status('bob')['status'], field_duty.NONE)

    def test_check_in_cannot_override_session_owner_with_body(self):
        client = self.login()
        sid = schedule_store.save(dict(day='2026-11-02', start='09:00', end='12:00', members=['bob']))
        with patch.object(schedule_store.system_clock, 'now', return_value=self.now):
            response = client.post(f'/api/schedules/{sid}/check-in', json={'username': 'bob', 'name': 'bob'})
        self.assertEqual(response.status_code, 404)
        with db.read() as conn:
            self.assertIsNone(conn.execute('SELECT checked_at FROM attendance WHERE shift_id=? AND name=?', (sid, 'bob')).fetchone()['checked_at'])

    def test_password_requires_old_password_and_uses_session_owner(self):
        client = self.login()
        old_session = self.login()
        for old in ('', 'wrong-password'):
            response = client.post('/api/member/password', json={'username': 'bob', 'old_password': old, 'new_password': 'new-password-456'})
            self.assertEqual(response.status_code, 400)
            self.assertTrue(member_store.verify('alice', 'password123'))
        response = client.post('/api/member/password', json={'username': 'bob', 'old_password': 'password123', 'new_password': 'new-password-456'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(member_store.verify('alice', 'new-password-456'))
        self.assertTrue(member_store.verify('bob', 'password123'))
        self.assertEqual(old_session.get('/api/member/field-duty').status_code, 401)
        self.assertEqual(client.get('/api/member/field-duty').status_code, 200)

    def test_data_console_masks_model_and_smtp_credentials(self):
        permissions.set_permissions('alice', ['data'])
        client = self.login(admin=True)
        with db.write() as conn:
            db.set_setting(conn, 'model', {'openai_key': 'MODEL_TEST_SECRET'})
            db.set_setting(conn, 'smtp', {'password': 'SMTP_TEST_SECRET'})
        response = client.get('/api/admin/data/tables/settings')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('MODEL_TEST_SECRET', response.text)
        self.assertNotIn('SMTP_TEST_SECRET', response.text)

    def test_limited_admin_cannot_access_other_admin_operations(self):
        permissions.set_permissions('alice', ['logs'])
        client = self.login(admin=True)
        exempt = {'/api/admin/status', '/api/admin/login', '/api/admin/register', '/api/admin/logout', '/api/admin/logs'}
        for route in api.app.routes:
            if not isinstance(route, APIRoute) or not route.path.startswith('/api/admin/') or route.path in exempt:
                continue
            path = re.sub(r'\{([^}]+)\}', lambda m: '1' if m[1] in ('shift_id', 'swap_id') else 'probe', route.path)
            for method in route.methods:
                response = client.request(method, path, json={})
                self.assertEqual(response.status_code, 403, (method, path, response.text))

    def test_member_ssrf_cannot_override_configured_destination(self):
        client = self.login()
        with patch.object(api.admin_store, 'mcp_url', return_value=''), patch.object(api, 'OutlineMcpClient') as upstream:
            response = client.post('/api/mcp/connect', json={'mcp_url': 'http://127.0.0.1:10000', 'mcp_api_key': 'x'})
            self.assertEqual(response.status_code, 400)
            upstream.assert_not_called()
        with patch.object(api.admin_store, 'mcp_url', return_value='https://wiki.example/mcp'), patch.object(api, 'OutlineMcpClient') as upstream:
            upstream.return_value.__aenter__.side_effect = RuntimeError('mock connection failure')
            response = client.post('/api/mcp/connect', json={'mcp_url': 'http://127.0.0.1:10000', 'mcp_api_key': 'x'})
            self.assertEqual(response.status_code, 502)
            upstream.assert_called_once_with('https://wiki.example/mcp', 'x')

    def test_special_only_admin_cannot_modify_regular_shift(self):
        permissions.set_permissions('alice', ['special'])
        client = self.login(admin=True)
        body = dict(day='2026-11-02', start='09:00', end='12:00', members=['bob'])
        sid = schedule_store.save(body)
        response = client.put(f'/api/admin/special-schedules/{sid}', json={**body, 'members': ['alice']})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(client.delete(f'/api/admin/special-schedules/{sid}').status_code, 404)
        with db.read() as conn:
            self.assertEqual([row['name'] for row in conn.execute('SELECT name FROM attendance WHERE shift_id=?', (sid,))], ['bob'])

    def test_secret_settings_cannot_be_overwritten_through_data_console(self):
        permissions.set_permissions('alice', ['data'])
        client = self.login(admin=True)
        with db.write() as conn:
            db.set_setting(conn, 'smtp', {'password': 'SMTP_TEST_SECRET'})
        response = client.put('/api/admin/data/tables/settings', json={'keys': {'key': 'smtp'}, 'values': {'value': '{}'}})
        self.assertEqual(response.status_code, 400)
        with db.read() as conn:
            self.assertEqual(db.get_setting(conn, 'smtp'), {'password': 'SMTP_TEST_SECRET'})

    def test_data_console_cannot_reopen_registration(self):
        """Clearing the bootstrap flag used to let /api/admin/register mint a root session."""
        from server import root_admin
        root_admin.create_if_missing('root-password-123')
        permissions.set_permissions('alice', ['data'])
        client = self.login(admin=True)
        with db.write() as conn:
            db.set_setting(conn, 'setup_done', root_admin.username())
        response = client.put('/api/admin/data/tables/settings',
                              json={'keys': {'key': 'setup_done'}, 'values': {'value': '""'}})
        self.assertEqual(response.status_code, 400)
        with db.read() as conn:
            self.assertEqual(db.get_setting(conn, 'setup_done'), root_admin.username())
        # Even with the flag gone the endpoint refuses once the root file exists.
        with db.write() as conn:
            conn.execute("DELETE FROM settings WHERE `key`='setup_done'")
        gate = TestClient(api.app)
        gate.get('/' + admin_store.path())
        self.assertEqual(gate.post('/api/admin/register', json={'password': 'brand-new-pass'}).status_code, 409)

    def test_data_console_cannot_disable_an_administrator(self):
        permissions.set_permissions('alice', ['data'])
        permissions.set_permissions('bob', ['overview'])
        client = self.login(admin=True)
        response = client.put('/api/admin/data/tables/member_accounts',
                              json={'keys': {'username': 'bob'}, 'values': {'active': 0}})
        self.assertEqual(response.status_code, 400)
        with db.read() as conn:
            self.assertEqual(conn.execute("SELECT active FROM member_accounts WHERE username='bob'").fetchone()['active'], 1)
        self.assertEqual(client.request('DELETE', '/api/admin/data/tables/member_accounts',
                                        json={'keys': {'username': 'bob'}}).status_code, 400)
        page = client.get('/api/admin/data/tables/member_accounts').json()
        self.assertFalse({row['username']: row for row in page['items']}['bob']['deletable'])

    def test_data_console_cannot_erase_the_audit_trail(self):
        permissions.set_permissions('alice', ['data'])
        client = self.login(admin=True)
        with db.write() as conn:
            conn.execute("INSERT INTO operation_log(at,actor,role,action,detail)"
                         " VALUES('2026-11-02T09:00:00+08:00','root','admin','测试','记录')")
        row = client.get('/api/admin/data/tables/operation_log').json()['items'][0]
        for method, path, body in [
                ('DELETE', '/api/admin/data/tables/operation_log', {'keys': {'id': row['id']}}),
                ('POST', '/api/admin/data/tables/operation_log/delete-many', {'keys': [{'id': row['id']}]}),
                ('POST', '/api/admin/data/tables/operation_log/delete-page', {'page': 1})]:
            self.assertEqual(client.request(method, path, json=body).status_code, 400, (method, path))
        ids = [item['id'] for item in client.get('/api/admin/data/tables/operation_log').json()['items']]
        self.assertIn(row['id'], ids)

    def test_settings_cards_do_not_expose_other_cards_credentials(self):
        with db.write() as conn:
            db.set_setting(conn, 'model', {'openai_key': 'MODEL_TEST_SECRET',
                                           'keys_by_provider': {'openai': 'PROVIDER_TEST_SECRET'}})
            db.set_setting(conn, 'qq', {'app_secret': 'QQ_TEST_SECRET'})
        permissions.set_permissions('alice', ['smtp'])
        client = self.login(admin=True)
        for path in ('/api/admin/model', '/api/admin/qq'):
            response = client.get(path)
            self.assertEqual(response.status_code, 200, path)
            for secret in ('MODEL_TEST_SECRET', 'PROVIDER_TEST_SECRET', 'QQ_TEST_SECRET'):
                self.assertNotIn(secret, response.text, (path, secret))
        # Saving the masked echo back keeps the stored keys untouched.
        self.assertEqual(client.post('/api/admin/model', json={'provider': 'openai'}).status_code, 200)
        self.assertEqual(admin_store.model_config()['openai_key'], 'MODEL_TEST_SECRET')
        self.assertEqual(admin_store.model_config()['keys_by_provider'], {'openai': 'PROVIDER_TEST_SECRET'})

    def test_member_login_is_throttled_per_source(self):
        self.login_client = TestClient(api.app)
        limiter = security.AttemptLimiter(max_attempts=security.MEMBER_MAX_ATTEMPTS,
                                          window=security.MEMBER_WINDOW_SECONDS)
        self.assertEqual(security.MEMBER_MAX_ATTEMPTS, 50)
        self.assertEqual(security.MEMBER_WINDOW_SECONDS, 5 * 60)
        with patch.object(api, 'MEMBER_LIMITER', limiter):
            with patch.object(api.member_store, 'authenticate', return_value=None):
                codes = [self.login_client.post('/api/member/login',
                                                json={'username': 'alice', 'password': 'wrong'}).status_code
                         for _ in range(52)]
        self.assertEqual(codes, [401] * 50 + [429] * 2)

    def test_data_console_cannot_touch_a_disabled_administrator_either(self):
        """`is_admin` is False for a disabled account, so the guard must read the stored row."""
        permissions.set_permissions('alice', ['data'])
        permissions.set_permissions('bob', ['overview'])
        member_store.update('bob', active=False)
        client = self.login(admin=True)
        response = client.put('/api/admin/data/tables/member_accounts',
                              json={'keys': {'username': 'bob'}, 'values': {'active': 1}})
        self.assertEqual(response.status_code, 400)
        with db.read() as conn:
            self.assertEqual(conn.execute("SELECT active FROM member_accounts WHERE username='bob'").fetchone()['active'], 0)
        self.assertEqual(client.request('DELETE', '/api/admin/data/tables/member_accounts',
                                        json={'keys': {'username': 'bob'}}).status_code, 400)
        with db.read() as conn:
            self.assertIsNotNone(conn.execute("SELECT 1 FROM member_accounts WHERE username='bob'").fetchone())

    def test_corrupt_checkin_rules_do_not_break_member_pages(self):
        client = self.login()
        with db.write() as conn:
            db.set_setting(conn, 'checkin_access', {'checkin': 'not-a-dict'})
        for path in ('/api/network-access', '/api/member/status', '/api/member/field-duty'):
            self.assertEqual(client.get(path).status_code, 200, path)
        self.assertEqual(client.get('/api/member/schedules', params={'day': '2026-11-02'}).status_code, 200)
        # The failure falls back to "no restriction" rather than blocking every check-in.
        self.assertTrue(client.get('/api/network-access').json()['checkin'])

    def test_logs_page_reports_the_storage_cap(self):
        permissions.set_permissions('alice', ['logs'])
        client = self.login(admin=True)
        from server import operation_log
        operation_log.record('alice', '登录管理端')
        body = client.get('/api/admin/logs').json()
        self.assertEqual(body['stats']['limit_bytes'], 10 * 1024 * 1024)
        self.assertEqual(body['stats']['keep_bytes'], 5 * 1024 * 1024)
        self.assertGreater(body['stats']['used_bytes'], 0)
        self.assertGreaterEqual(body['stats']['rows'], 1)

    def test_first_setup_accepts_a_chosen_root_name(self):
        from server import root_admin
        # Start from a genuinely unregistered site: the fixture marks setup as done.
        with db.write() as conn:
            conn.execute("DELETE FROM settings WHERE `key`='setup_done'")
            conn.execute('DELETE FROM users')
        client = TestClient(api.app)
        client.get('/' + admin_store.path())
        response = client.post('/api/admin/register',
                               json={'username': '协会负责人', 'password': 'first-password-123'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['username'], '协会负责人')
        self.assertEqual(root_admin.username(), '协会负责人')
        self.assertTrue(root_admin.verify('first-password-123'))
        # The chosen name is what the root account logs in with from then on.
        fresh = TestClient(api.app)
        fresh.get('/' + admin_store.path())
        self.assertEqual(fresh.post('/api/admin/login',
                                    json={'username': '协会负责人',
                                          'password': 'first-password-123'}).status_code, 200)
        # A second registration is still refused.
        self.assertEqual(client.post('/api/admin/register',
                                     json={'username': 'someone-else',
                                           'password': 'another-password'}).status_code, 409)

    def test_roster_validates_untrusted_json_before_creating_accounts(self):
        permissions.set_permissions('alice', ['weekly'])
        client = self.login(admin=True)
        roster = weekly_store.empty_roster()
        roster['slots'][0]['days'][0] = [{'__class__': 'malicious'}]
        response = client.put('/api/admin/weekly-roster', json=roster)
        self.assertEqual(response.status_code, 400)
        self.assertEqual({row['username'] for row in member_store.list_accounts()}, {'alice', 'bob'})
        with db.read() as conn:
            self.assertIsNone(db.get_setting(conn, weekly_store.KEY))

    def test_roster_sql_and_html_payloads_are_stored_as_plain_names(self):
        permissions.set_permissions('alice', ['weekly'])
        client = self.login(admin=True)
        roster = weekly_store.empty_roster()
        names = ["x'); DROP TABLE member_accounts;--", '<img onerror=alert(1)>', '__proto__']
        roster['slots'][0]['days'][0] = names
        response = client.put('/api/admin/weekly-roster', json=roster)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(weekly_store.load()['slots'][0]['days'][0], names)
        self.assertTrue(member_store.verify('bob', 'password123'))
