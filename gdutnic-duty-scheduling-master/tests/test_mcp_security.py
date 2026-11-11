import unittest
from unittest.mock import AsyncMock, patch

import httpx
from server import app as api, security, oauth
from server.mcp_client import McpError, OutlineMcpClient


class McpSecurityTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_anonymous_requests_cannot_fetch_or_seed_urls(self):
        with patch.object(api, '_admin_ok', return_value=False), patch.object(api, 'OutlineMcpClient') as client:
            for path in ['/api/mcp/connect', '/api/settings', '/api/mcp/restore', '/api/chat']:
                response = await self.client.post(path, json={'mcp_url': 'http://127.0.0.1:10000', 'mcp_api_key': 'x'})
                self.assertEqual(response.status_code, 401, path)
            response = await self.client.get('/api/mcp/oauth/start?mcp_url=http://127.0.0.1')
            self.assertEqual(response.status_code, 401)
            client.assert_not_called()

    async def test_body_and_old_session_urls_are_never_destinations(self):
        with patch.object(api.admin_store, 'mcp_url', return_value=''):
            self.assertEqual(api._resolve_mcp_url(None, {'mcp_url': 'http://127.0.0.1'}), '')
        with patch.object(api.admin_store, 'mcp_url', return_value='https://wiki.example/mcp'):
            self.assertEqual(api._resolve_mcp_url(None, {'mcp_url': 'http://127.0.0.1'}), 'https://wiki.example/mcp')

    async def test_connection_errors_do_not_disclose_upstream(self):
        with patch.object(api, '_require_mcp_user'), patch.object(api.admin_store, 'mcp_url', return_value='https://wiki.example/mcp'), patch.object(OutlineMcpClient, '__aenter__', new=AsyncMock(side_effect=McpError('INTERNAL_SECRET', status=500))):
            response = await self.client.post('/api/mcp/connect', json={'mcp_api_key': 'x'})
            self.assertEqual(response.status_code, 502)
            self.assertNotIn('INTERNAL_SECRET', response.text)

    async def test_oauth_metadata_cannot_choose_other_origin(self):
        with patch.object(oauth.admin_store, 'mcp_url', return_value='https://wiki.example/mcp'), patch.object(oauth.httpx, 'AsyncClient') as client:
            for url in ['http://127.0.0.1/token', 'https://evil.example/token', 'https://wiki.example:444/token', 'https://user@wiki.example/token']:
                with self.assertRaises(ValueError):
                    await oauth._wiki_request('POST', url)
            client.assert_not_called()

    async def test_rotating_forwarded_header_does_not_bypass_limit(self):
        with patch.object(api, 'ADMIN_LIMITER', security.AttemptLimiter()), patch.object(api, 'ACCOUNT_LIMITER', security.AttemptLimiter()), patch.object(api, '_admin_gate', return_value=True), patch.object(api.member_store, 'authenticate', return_value=None), patch.object(api.root_admin, 'is_root', return_value=False):
            codes = []
            for i in range(11):
                response = await self.client.post('/api/admin/login', json={'username': f'absent{i}', 'password': 'invalid'}, headers={'X-Forwarded-For': f'192.0.2.{i}'})
                codes.append(response.status_code)
            self.assertEqual(codes, [401] * 8 + [429] * 3)

    async def test_account_limit_survives_rotating_sources(self):
        with patch.object(api, 'ADMIN_LIMITER', security.AttemptLimiter()), patch.object(api, 'ACCOUNT_LIMITER', security.AttemptLimiter()), patch.object(api.security, 'client_key', side_effect=[str(i) for i in range(11)]), patch.object(api, '_admin_gate', return_value=True), patch.object(api.member_store, 'authenticate', return_value=None), patch.object(api.root_admin, 'is_root', return_value=False):
            codes = [(await self.client.post('/api/admin/login', json={'username': 'absent', 'password': 'invalid'})).status_code for _ in range(11)]
            self.assertEqual(codes, [401] * 8 + [429] * 3)

    async def test_mcp_redirect_is_rejected_without_body_disclosure(self):
        mcp = OutlineMcpClient('https://wiki.example/mcp', 'token')
        requests = []
        async def respond(request):
            requests.append(request)
            return httpx.Response(307, headers={'Location': 'http://127.0.0.1/mcp'}, text='SECRET')
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=False) as client:
            mcp._client = client
            with self.assertRaises(McpError) as caught:
                await mcp._rpc('initialize')
            self.assertNotIn('SECRET', str(caught.exception))
            self.assertEqual(len(requests), 1)

    async def test_authenticated_connection_uses_only_admin_target(self):
        entered = AsyncMock()
        entered.mcp_url = 'https://wiki.example/mcp'
        entered.server_info = {}
        entered.list_tools.return_value = []
        with patch.object(api, '_require_mcp_user'), patch.object(api.admin_store, 'mcp_url', return_value=entered.mcp_url), patch.object(api, 'OutlineMcpClient') as client:
            client.return_value.__aenter__.return_value = entered
            response = await self.client.post('/api/mcp/connect', json={'mcp_url': 'http://127.0.0.1:10000', 'mcp_api_key': 'token'})
            self.assertEqual(response.status_code, 200)
            client.assert_called_once_with('https://wiki.example/mcp', 'token')

    async def test_restored_url_cannot_seed_new_destination(self):
        with patch.object(api, '_require_mcp_user'), patch.object(api.admin_store, 'mcp_url', return_value=''):
            response = await self.client.post('/api/mcp/restore', json={'outline_token': 'token', 'mcp_url': 'http://127.0.0.1:10000'})
            self.assertEqual(response.status_code, 200)
            response = await self.client.post('/api/mcp/connect', json={})
            self.assertEqual(response.status_code, 400)

    async def test_oauth_start_ignores_query_url(self):
        flow = {'authorize_url': 'https://wiki.example/oauth/authorize'}
        with patch.object(api, '_require_mcp_user'), patch.object(api.admin_store, 'mcp_url', return_value='https://wiki.example/mcp'), patch.object(api, 'start_oauth', new=AsyncMock(return_value=flow)) as start:
            response = await self.client.get('/api/mcp/oauth/start?mcp_url=http://127.0.0.1:10000')
            self.assertEqual(response.status_code, 302)
            self.assertEqual(start.call_args.args[0], 'https://wiki.example/mcp')

    async def test_member_login_has_its_own_throttle_separate_from_admin_failures(self):
        limiter = security.AttemptLimiter()
        for _ in range(8):
            limiter.fail('admin:absent')
        member_limiter = security.AttemptLimiter(max_attempts=2, window=60)
        with patch.object(api, 'ACCOUNT_LIMITER', limiter), \
                patch.object(api, 'MEMBER_LIMITER', member_limiter), \
                patch.object(api.member_store, 'authenticate', return_value=None), patch.object(api.root_admin, 'is_root', return_value=False):
            # Administrator failures in the account limiter do not block member logins.
            for expected in (401, 401, 429, 429):
                response = await self.client.post('/api/member/login', json={'username': 'absent', 'password': 'invalid'})
                self.assertEqual(response.status_code, expected)
            with patch.object(api, 'MEMBER_LIMITER', security.AttemptLimiter()), \
                    patch.object(api.member_store, 'authenticate', return_value='version'), patch.object(api, '_log'):
                response = await self.client.post('/api/member/login', json={'username': 'absent', 'password': 'valid'})
                self.assertEqual(response.status_code, 200)
