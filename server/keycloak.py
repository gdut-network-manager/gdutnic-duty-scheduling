"""Keycloak authorization-code login and explicit local-account linking.

OAuth secrets and pending password proofs stay in the database; the session only
contains an opaque transaction handle. Links use issuer/sub, never display names.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from datetime import datetime
from urllib.parse import urlencode, urlsplit

import httpx
import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from server import session_policy, admin_store, db, member_store, permissions, root_admin, sso_settings

router = APIRouter(prefix='/api/auth/keycloak')
TTL = 10 * 60


def config():
    value = sso_settings.load()
    if not value['enabled']:
        raise ValueError('GDUTNIC 登录尚未启用')
    sso_settings.validate(value, require_complete=True)
    return value


def endpoint(config, url):
    """Discovery may not send credentials to another origin or protocol."""
    expected, parsed = urlsplit(config['issuer']), urlsplit(url)
    if ((parsed.scheme, parsed.hostname, parsed.port) != (expected.scheme, expected.hostname, expected.port)
            or parsed.username or parsed.password or parsed.fragment):
        raise ValueError('SSO 配置包含不受信任的端点')
    return url


async def discovery(config):
    async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
        response = await client.get(config['issuer'] + '/.well-known/openid-configuration')
        response.raise_for_status()
        meta = response.json()
    if meta.get('issuer') != config['issuer']:
        raise ValueError('SSO issuer 校验失败')
    for field in ('authorization_endpoint', 'token_endpoint', 'userinfo_endpoint', 'jwks_uri'):
        endpoint(config, meta[field])
    return meta


def _id(request):
    handle = request.session.get('keycloak_flow')
    if not isinstance(handle, str) or len(handle) != 64:
        raise HTTPException(400, 'SSO 登录已过期，请重新登录')
    return handle


def _load(conn, handle):
    row = conn.execute('SELECT data,expires_at FROM keycloak_flows WHERE id=?', (handle,)).fetchone()
    if not row or row['expires_at'] <= int(time.time()):
        raise HTTPException(400, 'SSO 登录已过期，请重新登录')
    return json.loads(row['data'])


def _save(conn, handle, flow):
    conn.execute('UPDATE keycloak_flows SET data=? WHERE id=?', (json.dumps(flow), handle))


def pending(request, conn):
    flow = _load(conn, _id(request))
    if flow.get('stage') not in {'pending', 'verified'}:
        raise HTTPException(400, '请先完成 GDUTNIC 登录')
    if flow.get('issuer') != config()['issuer']:
        raise HTTPException(400, 'SSO 配置已改变，请重新登录')
    return flow


def csrf(request, body, flow):
    origin = request.headers.get('origin')
    callback = urlsplit(config()['redirect_uri'])
    expected = f'{callback.scheme}://{callback.netloc}'
    if origin and origin != expected:
        raise HTTPException(403, '请求来源不正确')
    token = body.get('csrf_token')
    if not isinstance(token, str) or not secrets.compare_digest(token, flow['csrf']):
        raise HTTPException(403, '请求校验失败，请刷新后重试')


def identity_key(issuer, subject):
    return hashlib.sha256((issuer + '\0' + subject).encode()).hexdigest()


def account_key(conn, name, create=False):
    if root_admin.is_root(name):
        record = root_admin._load()
        if not record:
            return ''
        return hashlib.sha256(('root:' + record['created_at']).encode()).hexdigest()
    row = conn.execute('SELECT active,sso_id FROM member_accounts WHERE username=?', (name,)).fetchone()
    if not row or not row['active']:
        return ''
    value = row['sso_id']
    if not value and create:
        value = secrets.token_hex(32)
        conn.execute('UPDATE member_accounts SET sso_id=? WHERE username=?', (value, name))
    return value


def eligible(name, target):
    if target == 'admin':
        return permissions.is_admin(name)
    return not root_admin.is_root(name)


def link(conn, flow, name):
    key = identity_key(flow['issuer'], flow['subject'])
    current = account_key(conn, name, create=True)
    if not current or not eligible(name, flow['target']):
        raise HTTPException(403, '该账号已停用或没有此登录入口的权限')
    existing = conn.execute('SELECT username,account_key FROM keycloak_links WHERE identity_key=?', (key,)).fetchone()
    if existing and (existing['username'] != name or existing['account_key'] != current):
        raise HTTPException(409, '该 SSO 身份已有绑定，请联系管理员处理')
    owner = conn.execute('SELECT identity_key,account_key FROM keycloak_links WHERE username=?', (name,)).fetchone()
    if owner and owner['identity_key'] != key:
        if owner['account_key'] == current:
            raise HTTPException(409, '该本地账号已绑定其他 SSO 身份')
        # An account deleted and recreated under the same name is a new identity.
        conn.execute('DELETE FROM keycloak_links WHERE username=?', (name,))
    if not existing:
        conn.execute('INSERT INTO keycloak_links(identity_key,issuer,subject,username,account_key,linked_at) VALUES(?,?,?,?,?,?)',
                     (key, flow['issuer'], flow['subject'], name, current, datetime.now().isoformat(timespec='seconds')))


def unlink_account(name):
    """Revoke the link and SSO logins; future linking needs local-password proof."""
    if root_admin.is_root(name):
        raise ValueError('原始管理员不在用户管理中解除绑定')
    with db.write() as conn:
        row = conn.execute('SELECT * FROM keycloak_links WHERE username=?', (name,)).fetchone()
        account = conn.execute('SELECT sso_id FROM member_accounts WHERE username=?', (name,)).fetchone()
        if not row or not account or account['sso_id'] != row['account_key']:
            raise LookupError('该账号没有可解除的 SSO 绑定')
        conn.execute('DELETE FROM keycloak_links WHERE username=?', (name,))
        conn.execute('UPDATE member_accounts SET sso_auto_link_blocked=1 WHERE username=?', (name,))
        conn.execute("DELETE FROM login_sessions WHERE username=? AND sso_identity<>''", (name,))
        for pending_row in conn.execute('SELECT id,data FROM keycloak_flows').fetchall():
            flow = json.loads(pending_row['data'])
            if (flow.get('local_username') == name or
                    (flow.get('issuer') == row['issuer'] and flow.get('subject') == row['subject'])):
                conn.execute('DELETE FROM keycloak_flows WHERE id=?', (pending_row['id'],))
    return {'ok': True, 'username': name}


def finish(request, name, target, sso_identity):
    from server import app as api
    request.session.clear()
    if target == 'admin':
        request.session.update(admin_ok=True, admin_username=name,
                               admin_version=member_store.session_version(name), admin_gate=admin_store.path(),
                               admin_sid=session_policy.create("admin", name, sso_identity=sso_identity))
        destination = '/' + admin_store.path()
    else:
        request.session.update(member_username=name, member_version=member_store.session_version(name),
                               member_sid=session_policy.create("member", name, sso_identity=sso_identity))
        destination = '/'
    response = RedirectResponse(destination, status_code=303)
    # A previous account's independent admin cookie must not survive SSO login.
    api._clear_admin_cookie(response)
    if target == 'admin':
        api._set_admin_cookie(response, name, request.session["admin_sid"])
    api._log(name, '通过 GDUTNIC 登录', role='admin' if target == 'admin' else 'member')
    return response


@router.get('/status')
async def status():
    try:
        config()
        return JSONResponse({'enabled': True}, headers={'Cache-Control': 'no-store'})
    except ValueError:
        return JSONResponse({'enabled': False}, headers={'Cache-Control': 'no-store'})


@router.get('/start')
async def start(request: Request, target: str = 'member'):
    from server import app as api
    if target not in {'member', 'admin'}:
        raise HTTPException(400, '登录入口不正确')
    if target == 'admin' and not api._admin_gate(request):
        raise HTTPException(403, '请从管理员入口登录')
    try:
        cfg = config()
        meta = await discovery(cfg)
    except Exception:
        raise HTTPException(503, 'GDUTNIC 登录暂时不可用，请联系管理员检查配置')
    handle = secrets.token_hex(32)
    verifier = secrets.token_urlsafe(48)
    flow = {'stage': 'oauth', 'state': secrets.token_urlsafe(32), 'nonce': secrets.token_urlsafe(32),
            'verifier': verifier, 'target': target, 'issuer': cfg['issuer'], 'csrf': secrets.token_urlsafe(32)}
    with db.write() as conn:
        conn.execute('DELETE FROM keycloak_flows WHERE expires_at<=?', (int(time.time()),))
        old = request.session.get('keycloak_flow')
        if isinstance(old, str):
            conn.execute('DELETE FROM keycloak_flows WHERE id=?', (old,))
        conn.execute('INSERT INTO keycloak_flows(id,expires_at,data) VALUES(?,?,?)',
                     (handle, int(time.time()) + TTL, json.dumps(flow)))
    request.session['keycloak_flow'] = handle
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    params = dict(client_id=cfg['client_id'], redirect_uri=cfg['redirect_uri'], response_type='code',
                  scope='openid profile', state=flow['state'], nonce=flow['nonce'],
                  code_challenge=challenge, code_challenge_method='S256')
    return RedirectResponse(meta['authorization_endpoint'] + '?' + urlencode(params), status_code=303)


async def exchange(cfg, flow, code):
    meta = await discovery(cfg)
    data = dict(grant_type='authorization_code', code=code, redirect_uri=cfg['redirect_uri'],
                client_id=cfg['client_id'], code_verifier=flow['verifier'])
    if cfg['secret']:
        data['client_secret'] = cfg['secret']
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        response = await client.post(meta['token_endpoint'], data=data)
        response.raise_for_status()
        tokens = response.json()
        response = await client.get(meta['jwks_uri'])
        response.raise_for_status()
        keys = response.json()['keys']
        header = jwt.get_unverified_header(tokens['id_token'])
        algorithm = header.get('alg')
        if algorithm not in {'RS256', 'RS384', 'RS512', 'PS256', 'PS384', 'PS512', 'ES256', 'ES384', 'ES512', 'EdDSA'}:
            raise ValueError('不支持的 SSO 签名算法')
        matches = [key for key in keys if key.get('kid') == header.get('kid')
                   and key.get('use', 'sig') == 'sig' and key.get('alg', algorithm) == algorithm]
        if len(matches) != 1:
            raise ValueError('SSO 签名密钥不正确')
        claims = jwt.decode(tokens['id_token'], jwt.PyJWK.from_dict(matches[0]).key,
                            algorithms=[algorithm], audience=cfg['client_id'], issuer=cfg['issuer'],
                            options={'require': ['exp', 'iat', 'iss', 'aud', 'sub', 'nonce']}, leeway=30)
        if claims['nonce'] != flow['nonce'] or not isinstance(claims['sub'], str) or not 1 <= len(claims['sub']) <= 255:
            raise ValueError('SSO 身份校验失败')
        if (isinstance(claims['aud'], list) and len(claims['aud']) > 1 or 'azp' in claims) and claims.get('azp') != cfg['client_id']:
            raise ValueError('SSO 客户端校验失败')
        # The token may omit profile fields. UserInfo can supply them, but must agree on sub.
        response = await client.get(meta['userinfo_endpoint'], headers={'Authorization': 'Bearer ' + tokens['access_token']})
        if response.status_code == 200:
            profile = response.json()
            if profile.get('sub') != claims['sub']:
                raise ValueError('SSO UserInfo 身份不一致')
            claims = {**claims, **{k: v for k, v in profile.items() if k not in {'iss', 'sub', 'aud', 'nonce', 'exp', 'iat', 'azp'}}}
    return claims


@router.get('/callback')
async def callback(request: Request, code: str = '', state: str = '', error: str = ''):
    handle = _id(request)
    with db.write() as conn:
        flow = _load(conn, handle)
        if flow.get('stage') != 'oauth' or not state or not secrets.compare_digest(state, flow['state']):
            raise HTTPException(400, 'SSO 登录校验失败，请重新登录')
        flow['stage'] = 'exchanging'
        _save(conn, handle, flow)
    if error or not code:
        raise HTTPException(400, 'GDUTNIC 登录已取消或失败，请重新登录')
    try:
        cfg = config()
        if flow['issuer'] != cfg['issuer']:
            raise ValueError('issuer changed')
        claims = await exchange(cfg, flow, code)
    except Exception:
        raise HTTPException(400, 'GDUTNIC 身份验证失败，请重新登录')
    profile = {key: claims[key] for key in ('preferred_username', 'name', 'given_name', 'family_name', cfg['claim'])
               if key and isinstance(claims.get(key), str) and len(claims[key]) <= 255}
    flow = {k: flow[k] for k in ('target', 'issuer', 'csrf')}
    flow.update(stage='pending', subject=claims['sub'], profile=profile)
    with db.write() as conn:
        # Refuse completion if the flow expired during the network request.
        _load(conn, handle)
        row = conn.execute('SELECT username,account_key FROM keycloak_links WHERE identity_key=?',
                           (identity_key(flow['issuer'], flow['subject']),)).fetchone()
        name = row['username'] if row else ''
        if row:
            if account_key(conn, name) != row['account_key'] or not eligible(name, flow['target']):
                raise HTTPException(403, '绑定账号已停用、被重建或没有此入口权限')
        elif cfg['claim']:
            match = profile.get(cfg['claim'], '').strip()
            # Automatic matching may only ever pick up an ordinary member account.
            # Privileged accounts (anything holding an administrative permission, and the
            # root account) must prove the local password, otherwise whoever can edit the
            # SSO settings could point the claim at an administrator name and take it over.
            if match and not root_admin.is_root(match) and not permissions.is_admin(match):
                candidate = conn.execute('SELECT username FROM member_accounts WHERE username=? AND active=1 AND sso_auto_link_blocked=0', (match,)).fetchone()
                if candidate and eligible(match, flow['target']):
                    # Never silently replace an existing link.
                    owner = conn.execute('SELECT 1 FROM keycloak_links WHERE username=?', (match,)).fetchone()
                    if not owner:
                        link(conn, flow, match)
                        name = match
        if name:
            conn.execute('DELETE FROM keycloak_flows WHERE id=?', (handle,))
        else:
            _save(conn, handle, flow)
    if name:
        return finish(request, name, flow['target'], identity_key(flow['issuer'], flow['subject']))
    return RedirectResponse('/auth/keycloak/bind', status_code=303)


@router.get('/binding')
async def binding(request: Request):
    with db.read() as conn:
        flow = pending(request, conn)
    profile = flow['profile']
    display = profile.get('preferred_username') or profile.get('name') or '未返回用户名的 SSO 账号'
    return {'sso_username': display, 'profile': profile, 'username_received': bool(profile.get('preferred_username')),
            'local_username': flow.get('local_username', ''), 'csrf_token': flow['csrf']}


@router.post('/verify')
async def verify(request: Request):
    from server import app as api
    body = await request.json()
    with db.read() as conn:
        flow = pending(request, conn)
    csrf(request, body, flow)
    name, password = str(body.get('username') or '').strip(), str(body.get('password') or '')
    key, account = 'admin:' + api.security.login_client_key(request), 'admin:' + name.casefold()
    # A binding proof that can authorize admin login uses the existing admin throttle.
    privileged = flow['target'] == 'admin' or permissions.is_admin(name)
    if privileged and (api.ADMIN_LIMITER.blocked(key) or api.ACCOUNT_LIMITER.blocked(account)):
        raise HTTPException(429, '管理员登录尝试过于频繁，请在限制开始 10 分钟后重试')
    version = (root_admin.session_version() if root_admin.verify(password) else None) if root_admin.is_root(name) else member_store.authenticate(name, password)
    if not version or not eligible(name, flow['target']):
        if privileged:
            api.ADMIN_LIMITER.fail(key)
            api.ACCOUNT_LIMITER.fail(account)
        raise HTTPException(401, '姓名或密码不正确，或账号没有此入口权限')
    if privileged:
        api.ADMIN_LIMITER.reset(key)
        api.ACCOUNT_LIMITER.reset(account)
    with db.write() as conn:
        flow = pending(request, conn)
        csrf(request, body, flow)
        flow.update(stage='verified', local_username=name, local_version=version)
        _save(conn, _id(request), flow)
    return {'sso_username': flow['profile'].get('preferred_username') or flow['profile'].get('name') or '未返回用户名的 SSO 账号',
            'local_username': name}


@router.post('/confirm')
async def confirm(request: Request):
    body = await request.json()
    with db.write() as conn:
        flow = pending(request, conn)
        csrf(request, body, flow)
        name = flow.get('local_username')
        if flow['stage'] != 'verified' or not name or not member_store.session_valid(name, flow.get('local_version'), use_cache=False):
            raise HTTPException(400, '请重新验证本地账号密码')
        link(conn, flow, name)
        conn.execute('DELETE FROM keycloak_flows WHERE id=?', (_id(request),))
    response = finish(request, name, flow['target'], identity_key(flow['issuer'], flow['subject']))
    # fetch cannot navigate the browser when it receives a redirect.
    result = JSONResponse({'ok': True, 'redirect': response.headers['location']})
    for key, value in response.raw_headers:
        if key == b'set-cookie':
            result.raw_headers.append((key, value))
    return result
