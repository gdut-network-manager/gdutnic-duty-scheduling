"""SSO flows with real local accounts and signed OIDC tokens; no live credentials."""
import asyncio
import json
import os
from pathlib import Path
import time
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from test_permissions import PermissionFixture
from server import app as api, admin_store, db, keycloak, member_store, permissions, root_admin, security

ISSUER = 'https://sso.gdutnic.com/realms/nic'
CALLBACK = 'https://app.example/api/auth/keycloak/callback'
META = {'issuer': ISSUER, **{key: ISSUER + '/protocol/openid-connect/' + value for key, value in {
    'authorization_endpoint': 'auth', 'token_endpoint': 'token', 'userinfo_endpoint': 'userinfo', 'jwks_uri': 'certs'}.items()}}


class KeycloakTest(PermissionFixture):
    def setUp(self):
        super().setUp()
        self.env2 = patch.dict(os.environ, {'KEYCLOAK_ENABLED': 'true', 'KEYCLOAK_ISSUER': ISSUER, 'KEYCLOAK_CLIENT_ID': 'duty',
                                          'KEYCLOAK_REDIRECT_URI': CALLBACK, 'KEYCLOAK_CLIENT_SECRET': '',
                                          'KEYCLOAK_USERNAME_CLAIM': ''})
        self.env2.start()
        self.addCleanup(self.env2.stop)
        self.mock_discovery = patch.object(keycloak, 'discovery', new=AsyncMock(return_value=META))
        self.mock_discovery.start()
        self.addCleanup(self.mock_discovery.stop)
        for name in ('ADMIN_LIMITER', 'ACCOUNT_LIMITER'):
            item = patch.object(api, name, security.AttemptLimiter())
            item.start()
            self.addCleanup(item.stop)
        member_store.create('alice', 'password123')
        member_store.create('bob', 'password123')
        admin_store.remember_setup('alice')
        self.client = TestClient(api.app, base_url='https://app.example')
        self.addCleanup(self.client.close)

    def begin(self, client=None, target='member'):
        client = client or self.client
        response = client.get('/api/auth/keycloak/start', params={'target': target}, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        params = parse_qs(urlsplit(response.headers['location']).query)
        self.assertEqual(params['code_challenge_method'], ['S256'])
        return params

    def callback(self, profile=None, client=None, target='member'):
        client = client or self.client
        params = self.begin(client, target)
        with patch.object(keycloak, 'exchange', new=AsyncMock(return_value=profile or {'sub': 'subject-1', 'preferred_username': 'sso-alice'})):
            response = client.get('/api/auth/keycloak/callback', params={'state': params['state'][0], 'code': 'valid-code'}, follow_redirects=False)
        return response

    def verify(self, name='alice', password='password123'):
        status = self.client.get('/api/auth/keycloak/binding').json()
        return self.client.post('/api/auth/keycloak/verify', json={'username': name, 'password': password, 'csrf_token': status['csrf_token']}), status

    def test_manual_binding_requires_password_then_explicit_confirmation(self):
        response = self.callback()
        self.assertEqual(response.headers['location'], '/auth/keycloak/bind')
        self.assertFalse(self.client.get('/api/member/status').json()['logged_in'])
        response, status = self.verify(password='wrong')
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.post('/api/auth/keycloak/confirm', json={'csrf_token': status['csrf_token']}).status_code, 400)
        response, status = self.verify()
        self.assertEqual(response.json(), {'sso_username': 'sso-alice', 'local_username': 'alice'})
        with db.read() as conn:
            self.assertIsNone(conn.execute('SELECT username FROM keycloak_links').fetchone())
        response = self.client.post('/api/auth/keycloak/confirm', json={'csrf_token': status['csrf_token']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get('/api/member/status').json()['username'], 'alice')
        self.assertEqual(self.client.post('/api/auth/keycloak/confirm', json={'csrf_token': status['csrf_token']}).status_code, 400)
        # Repeated SSO login uses stable sub even if the displayed username changes.
        response = self.callback({'sub': 'subject-1', 'preferred_username': 'renamed'})
        self.assertEqual(response.headers['location'], '/')
        self.assertEqual(self.client.get('/api/member/status').json()['username'], 'alice')

    def test_configured_claim_can_match_existing_local_account(self):
        with patch.dict(os.environ, {'KEYCLOAK_USERNAME_CLAIM': 'preferred_username'}):
            response = self.callback({'sub': 'subject-1', 'preferred_username': 'alice'})
        self.assertEqual(response.headers['location'], '/')
        self.assertEqual(self.client.get('/api/member/status').json()['username'], 'alice')

    def test_automatic_matching_never_links_a_privileged_account(self):
        """Whoever edits the SSO settings could point the claim at an administrator name.

        An administrator is only reachable through the password-verified binding page, so
        the settings permission alone can never turn an SSO identity into an admin session.
        """
        member_store.create('ops', 'password123')
        permissions.set_permissions('ops', ['overview'])
        self.assertTrue(permissions.is_admin('ops'))
        with patch.dict(os.environ, {'KEYCLOAK_USERNAME_CLAIM': 'preferred_username'}):
            response = self.callback({'sub': 'attacker-sub', 'preferred_username': 'ops'})
        self.assertEqual(response.headers['location'], '/auth/keycloak/bind')
        self.assertFalse(self.client.get('/api/member/status').json()['logged_in'])
        with db.read() as conn:
            self.assertIsNone(conn.execute('SELECT username FROM keycloak_links').fetchone())

    def test_automatic_matching_never_links_the_root_account(self):
        root_admin.create_if_missing('root-password-123')
        with patch.dict(os.environ, {'KEYCLOAK_USERNAME_CLAIM': 'preferred_username'}):
            response = self.callback({'sub': 'root-sub', 'preferred_username': root_admin.username()})
        self.assertEqual(response.headers['location'], '/auth/keycloak/bind')
        with db.read() as conn:
            self.assertIsNone(conn.execute('SELECT username FROM keycloak_links').fetchone())

    def test_missing_username_uses_manual_binding_without_losing_subject(self):
        response = self.callback({'sub': 'subject-with-no-name'})
        self.assertEqual(response.headers['location'], '/auth/keycloak/bind')
        response, status = self.verify()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post('/api/auth/keycloak/confirm', json={'csrf_token': status['csrf_token']}).status_code, 200)
        with db.read() as conn:
            self.assertEqual(conn.execute('SELECT subject FROM keycloak_links').fetchone()['subject'], 'subject-with-no-name')

    def test_callback_state_is_bound_to_the_browser_and_one_use(self):
        params = self.begin()
        for client, state in [(TestClient(api.app), params['state'][0]), (self.client, 'wrong-state')]:
            response = client.get('/api/auth/keycloak/callback', params={'state': state, 'code': 'code'})
            self.assertEqual(response.status_code, 400)
        with patch.object(keycloak, 'exchange', new=AsyncMock(return_value={'sub': 's'})) as exchange:
            for expected in (303, 400):
                response = self.client.get('/api/auth/keycloak/callback', params={'state': params['state'][0], 'code': 'code'}, follow_redirects=False)
                self.assertEqual(response.status_code, expected)
            exchange.assert_awaited_once()

    def test_binding_rejects_csrf_and_password_change_after_verification(self):
        self.callback()
        self.assertEqual(self.client.post('/api/auth/keycloak/verify', json={'username': 'alice', 'password': 'password123'}).status_code, 403)
        response, status = self.verify()
        self.assertEqual(response.status_code, 200)
        member_store.set_password('alice', 'new-password-456')
        self.assertEqual(self.client.post('/api/auth/keycloak/confirm', json={'csrf_token': status['csrf_token']}).status_code, 400)

    def test_identity_and_account_links_cannot_be_silently_replaced(self):
        self.callback()
        _, status = self.verify()
        self.assertEqual(self.client.post('/api/auth/keycloak/confirm', json={'csrf_token': status['csrf_token']}).status_code, 200)
        self.callback({'sub': 'subject-2', 'preferred_username': 'other'})
        _, status = self.verify()
        self.assertEqual(self.client.post('/api/auth/keycloak/confirm', json={'csrf_token': status['csrf_token']}).status_code, 409)

    def test_disabled_or_recreated_account_cannot_use_old_sso_link(self):
        with patch.dict(os.environ, {'KEYCLOAK_USERNAME_CLAIM': 'preferred_username'}):
            self.callback({'sub': 's', 'preferred_username': 'alice'})
        member_store.update('alice', active=False)
        self.assertEqual(self.callback({'sub': 's'}).status_code, 403)
        member_store.delete('alice')
        member_store.create('alice', 'other-password')
        self.assertEqual(self.callback({'sub': 's'}).status_code, 403)

    def test_sso_admin_entry_keeps_gate_and_local_permissions(self):
        response = self.client.get('/api/auth/keycloak/start?target=admin')
        self.assertEqual(response.status_code, 403)
        self.client.get('/' + admin_store.path())
        self.callback(target='admin')
        response, _ = self.verify()
        self.assertEqual(response.status_code, 401)
        permissions.set_permissions('alice', ['logs'])
        response, status = self.verify()
        self.assertEqual(response.status_code, 200)
        response = self.client.post('/api/auth/keycloak/confirm', json={'csrf_token': status['csrf_token']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/admin/logs').status_code, 200)
        self.assertEqual(self.client.get('/api/admin/members').status_code, 403)

    def test_root_first_binding_requires_password_and_admin_entry(self):
        root_admin.create_if_missing('root-password')
        self.client.get('/' + admin_store.path())
        with patch.dict(os.environ, {'KEYCLOAK_USERNAME_CLAIM': 'preferred_username'}):
            response = self.callback({'sub': 'root-sub', 'preferred_username': root_admin.username()}, target='admin')
        self.assertEqual(response.headers['location'], '/auth/keycloak/bind')
        response, status = self.verify(root_admin.username(), 'root-password')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post('/api/auth/keycloak/confirm', json={'csrf_token': status['csrf_token']}).status_code, 200)
        self.assertTrue(self.client.get('/api/admin/status').json()['root'])

    def test_pending_binding_expires(self):
        self.callback()
        with patch.object(keycloak.time, 'time', return_value=time.time() + keycloak.TTL + 1):
            self.assertEqual(self.client.get('/api/auth/keycloak/binding').status_code, 400)

    def test_signed_tokens_validate_signature_issuer_audience_nonce_and_userinfo_subject(self):
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
        public.update(kid='test-key', alg='RS256')
        valid = {'sub': 's', 'iss': ISSUER, 'aud': 'duty', 'exp': int(time.time()) + 300,
                 'iat': int(time.time()), 'nonce': 'expected-nonce'}
        real_client = httpx.AsyncClient
        cases = [{}, {'iss': 'https://evil.example'}, {'aud': 'other-client'},
                 {'nonce': 'wrong'}, {'exp': int(time.time()) - 120}, {'azp': 'other-client'},
                 {'test_invalid_signature': True}, {'test_userinfo_mismatch': True}]
        for change in cases:
            bad_signature = change.get('test_invalid_signature')
            signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048) if bad_signature else private
            token = jwt.encode({**valid, **change}, signing_key, algorithm='RS256', headers={'kid': 'test-key'})
            def respond(request):
                if request.url.path.endswith('/token'):
                    return httpx.Response(200, json={'id_token': token, 'access_token': 'test-access'})
                if request.url.path.endswith('/certs'):
                    return httpx.Response(200, json={'keys': [public]})
                return httpx.Response(200, json={'sub': 'other' if change.get('test_userinfo_mismatch') else 's', 'preferred_username': 'alice'})
            def factory(**kwargs):
                return real_client(transport=httpx.MockTransport(respond), **kwargs)
            with patch.object(keycloak.httpx, 'AsyncClient', side_effect=factory):
                if change:
                    with self.assertRaises((jwt.PyJWTError, ValueError)):
                        asyncio.run(keycloak.exchange(keycloak.config(), {'nonce': 'expected-nonce', 'verifier': 'v'}, 'code'))
                else:
                    claims = asyncio.run(keycloak.exchange(keycloak.config(), {'nonce': 'expected-nonce', 'verifier': 'v'}, 'code'))
                    self.assertEqual(claims['preferred_username'], 'alice')

    def settings_client(self):
        permissions.set_permissions('alice', ['sso'])
        self.client.get('/' + admin_store.path())
        response = self.client.post('/api/admin/login', json={'username': 'alice', 'password': 'password123'})
        self.assertEqual(response.status_code, 200)
        return self.client

    def test_sso_is_disabled_by_default_even_with_legacy_environment_fields(self):
        with patch.dict(os.environ, {'KEYCLOAK_ENABLED': ''}):
            self.assertFalse(self.client.get('/api/auth/keycloak/status').json()['enabled'])
            self.assertEqual(self.client.get('/api/auth/keycloak/start', follow_redirects=False).status_code, 503)

    def test_a_fresh_checkout_ships_no_identity_provider_address(self):
        """No organisation-specific default: a new deployment must type its own issuer."""
        from server import sso_settings
        keys = ('KEYCLOAK_ENABLED', 'KEYCLOAK_ISSUER', 'KEYCLOAK_CLIENT_ID',
                'KEYCLOAK_CLIENT_SECRET', 'KEYCLOAK_REDIRECT_URI', 'KEYCLOAK_USERNAME_CLAIM')
        with patch.dict(os.environ, {key: '' for key in keys}):
            with db.write() as conn:
                conn.execute("DELETE FROM settings WHERE `key`=?", (sso_settings.KEY,))
            value = sso_settings.load()
            public = sso_settings.public()
        self.assertEqual(value['issuer'], '')
        self.assertEqual(public['issuer'], '')
        self.assertFalse(value['enabled'])
        # And it still refuses to switch on without an address.
        with self.assertRaises(ValueError):
            sso_settings.validate(value, require_complete=True)
        # The shipped template must not carry an address either.
        template = (Path(__file__).resolve().parent.parent / '.env.example').read_text(encoding='utf-8')
        self.assertIn('KEYCLOAK_ISSUER=\n', template)

    def test_admin_settings_are_live_and_override_environment(self):
        client = self.settings_client()
        value = {'enabled': True, 'issuer': ISSUER, 'client_id': 'saved-client', 'client_type': 'public',
                 'redirect_uri': CALLBACK, 'claim': 'preferred_username'}
        response = client.put('/api/admin/sso', json=value)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(keycloak.config()['client_id'], 'saved-client')
        self.assertTrue(client.get('/api/auth/keycloak/status').json()['enabled'])
        self.assertEqual(client.put('/api/admin/sso', json={'enabled': False}).status_code, 200)
        self.assertFalse(client.get('/api/auth/keycloak/status').json()['enabled'])

    def test_settings_never_return_secret_and_preserve_blank_secret(self):
        client = self.settings_client()
        body = {'enabled': True, 'issuer': ISSUER, 'client_id': 'saved-client', 'client_type': 'confidential',
                'redirect_uri': CALLBACK, 'secret': 'TEST_CLIENT_SECRET'}
        response = client.put('/api/admin/sso', json=body)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('TEST_CLIENT_SECRET', response.text)
        self.assertTrue(response.json()['has_secret'])
        self.assertNotIn('TEST_CLIENT_SECRET', client.get('/api/admin/sso').text)
        self.assertEqual(client.put('/api/admin/sso', json={'secret': ''}).status_code, 200)
        self.assertEqual(keycloak.config()['secret'], 'TEST_CLIENT_SECRET')
        self.assertEqual(client.put('/api/admin/sso', json={'clear_secret': True}).status_code, 400)
        self.assertEqual(client.put('/api/admin/sso', json={'enabled': False, 'clear_secret': True}).status_code, 200)
        self.assertFalse(client.get('/api/admin/sso').json()['has_secret'])

    def test_incomplete_config_can_save_as_draft_but_not_enable(self):
        client = self.settings_client()
        self.assertEqual(client.put('/api/admin/sso', json={'enabled': False, 'client_id': '', 'redirect_uri': ''}).status_code, 200)
        self.assertEqual(client.put('/api/admin/sso', json={'enabled': True}).status_code, 400)
        self.assertFalse(client.get('/api/auth/keycloak/status').json()['enabled'])
        self.assertEqual(client.put('/api/admin/sso', json={'issuer': 'https://user:pass@sso.example'}).status_code, 400)

    def test_connection_check_does_not_enable_or_save_configuration(self):
        client = self.settings_client()
        with patch.dict(os.environ, {'KEYCLOAK_ENABLED': ''}):
            result = client.post('/api/admin/sso/test', json={'issuer': ISSUER, 'client_id': 'draft', 'redirect_uri': CALLBACK, 'client_type': 'public'})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertFalse(client.get('/api/auth/keycloak/status').json()['enabled'])
            with db.read() as conn:
                self.assertIsNone(db.get_setting(conn, 'sso'))

    def test_sso_settings_have_independent_permission(self):
        client = self.settings_client()
        permissions.set_permissions('alice', ['smtp'])
        for method, route in [('GET', '/api/admin/sso'), ('PUT', '/api/admin/sso'), ('POST', '/api/admin/sso/test')]:
            self.assertEqual(client.request(method, route, json={}).status_code, 403)

    def test_saved_sso_secret_is_protected_in_data_console(self):
        from server import data_admin
        client = self.settings_client()
        response = client.put('/api/admin/sso', json={'enabled': False, 'client_type': 'confidential', 'secret': 'SSO_PROTECTED_TEST_SECRET'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('SSO_PROTECTED_TEST_SECRET', json.dumps(data_admin.preview('settings')))
        with self.assertRaises(ValueError):
            data_admin.update_row('settings', {'key': 'sso'}, {'value': '{}'})

    def test_changing_settings_invalidates_unfinished_sso_flow(self):
        client = self.settings_client()
        self.callback()
        self.assertEqual(client.get('/api/auth/keycloak/binding').status_code, 200)
        self.assertEqual(client.put('/api/admin/sso', json={'enabled': False}).status_code, 200)
        self.assertEqual(client.get('/api/auth/keycloak/binding').status_code, 400)

    def bind_alice_for_unlink(self):
        with patch.dict(os.environ, {'KEYCLOAK_USERNAME_CLAIM': 'preferred_username'}):
            response = self.callback({'sub': 'unlink-subject', 'preferred_username': 'alice'})
        self.assertEqual(response.status_code, 303)
        permissions.set_permissions('bob', ['members'])
        manager = TestClient(api.app, base_url='https://app.example')
        self.addCleanup(manager.close)
        manager.get('/' + admin_store.path())
        self.assertEqual(manager.post('/api/admin/login', json={'username': 'bob', 'password': 'password123'}).status_code, 200)
        return manager

    def test_member_list_reports_binding_without_exposing_identity(self):
        manager = self.bind_alice_for_unlink()
        response = manager.get('/api/admin/members')
        items = {item['username']: item for item in response.json()['items']}
        self.assertTrue(items['alice']['sso_bound'])
        self.assertFalse(items['bob']['sso_bound'])
        self.assertNotIn('unlink-subject', response.text)
        self.assertNotIn('identity_key', response.text)

    def test_unlink_revokes_sso_sessions_but_keeps_password_sessions(self):
        manager = self.bind_alice_for_unlink()
        password_client = TestClient(api.app)
        self.addCleanup(password_client.close)
        self.assertEqual(password_client.post('/api/member/login', json={'username': 'alice', 'password': 'password123'}).status_code, 200)
        response = manager.delete('/api/admin/members/sso?username=alice')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(self.client.get('/api/member/status').json()['logged_in'])
        self.assertTrue(password_client.get('/api/member/status').json()['logged_in'])
        self.assertFalse(next(item for item in manager.get('/api/admin/members').json()['items'] if item['username'] == 'alice')['sso_bound'])
        self.assertEqual(manager.delete('/api/admin/members/sso?username=alice').status_code, 404)

    def test_unlinked_account_requires_password_proof_even_when_auto_matching_enabled(self):
        manager = self.bind_alice_for_unlink()
        self.assertEqual(manager.delete('/api/admin/members/sso?username=alice').status_code, 200)
        with patch.dict(os.environ, {'KEYCLOAK_USERNAME_CLAIM': 'preferred_username'}):
            response = self.callback({'sub': 'unlink-subject', 'preferred_username': 'alice'})
        self.assertEqual(response.headers['location'], '/auth/keycloak/bind')
        with db.read() as conn:
            self.assertIsNone(conn.execute('SELECT username FROM keycloak_links WHERE username=?', ('alice',)).fetchone())
        response, status = self.verify()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post('/api/auth/keycloak/confirm', json={'csrf_token': status['csrf_token']}).status_code, 200)
        self.assertTrue(self.client.get('/api/member/status').json()['logged_in'])

    def test_unlink_requires_user_management_permission(self):
        manager = self.bind_alice_for_unlink()
        self.assertEqual(self.client.delete('/api/admin/members/sso?username=alice').status_code, 401)
        permissions.set_permissions('bob', ['logs'])
        self.assertEqual(manager.delete('/api/admin/members/sso?username=alice').status_code, 403)
        self.assertTrue(self.client.get('/api/member/status').json()['logged_in'])

    def test_disabled_member_can_be_unlinked(self):
        manager = self.bind_alice_for_unlink()
        member_store.update('alice', active=False)
        self.assertEqual(manager.delete('/api/admin/members/sso?username=alice').status_code, 200)

    def test_different_sso_username_still_shows_bound_and_can_unlink(self):
        response = self.callback({'sub': 'different-name-subject', 'preferred_username': 'gdutnic_login_7788', 'name': 'SSO 显示姓名'})
        self.assertEqual(response.headers['location'], '/auth/keycloak/bind')
        response, status = self.verify('alice', 'password123')
        self.assertEqual(response.json()['sso_username'], 'gdutnic_login_7788')
        self.assertEqual(self.client.post('/api/auth/keycloak/confirm', json={'csrf_token': status['csrf_token']}).status_code, 200)
        permissions.set_permissions('bob', ['members'])
        manager = TestClient(api.app, base_url='https://app.example')
        self.addCleanup(manager.close)
        manager.get('/' + admin_store.path())
        self.assertEqual(manager.post('/api/admin/login', json={'username': 'bob', 'password': 'password123'}).status_code, 200)
        member = next(item for item in manager.get('/api/admin/members').json()['items'] if item['username'] == 'alice')
        self.assertTrue(member['sso_bound'])
        self.assertEqual(member['username'], 'alice')
        self.assertEqual(manager.delete('/api/admin/members/sso?username=alice').status_code, 200)
        self.assertFalse(next(item for item in manager.get('/api/admin/members').json()['items'] if item['username'] == 'alice')['sso_bound'])
