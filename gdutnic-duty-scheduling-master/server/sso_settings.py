"""Administrator-managed SSO configuration; disabled until explicitly enabled."""
import os
from urllib.parse import urlsplit
from server import db

KEY = 'sso'
FIELDS = ('issuer', 'client_id', 'client_type', 'secret', 'redirect_uri', 'claim')


def load():
    with db.read() as conn:
        saved = db.get_setting(conn, KEY)
    if isinstance(saved, dict):
        return {'enabled': False, **{k: '' for k in FIELDS}, **saved}
    return {
        'enabled': os.getenv('KEYCLOAK_ENABLED', '').lower() in {'1', 'true', 'yes'},
        'issuer': os.getenv('KEYCLOAK_ISSUER', ''),
        'client_id': os.getenv('KEYCLOAK_CLIENT_ID', ''),
        'client_type': 'confidential' if os.getenv('KEYCLOAK_CLIENT_SECRET') else 'public',
        'secret': os.getenv('KEYCLOAK_CLIENT_SECRET', ''),
        'redirect_uri': os.getenv('KEYCLOAK_REDIRECT_URI', ''),
        'claim': os.getenv('KEYCLOAK_USERNAME_CLAIM', ''),
    }


def validate(value, require_complete=False):
    if value['client_type'] not in {'public', 'confidential'}:
        raise ValueError('请选择公开客户端或机密客户端')
    for field, label in [('issuer', '身份提供方地址'), ('redirect_uri', '登录回调地址')]:
        url = value[field]
        if not url:
            if require_complete:
                raise ValueError(f'请填写{label}')
            continue
        parsed = urlsplit(url)
        local = parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1', '::1'}
        try:
            parsed.port
        except ValueError:
            raise ValueError(f'{label}的端口格式不正确') from None
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or (parsed.scheme != 'https' and not local)):
            raise ValueError(f'{label}须使用 HTTPS，且不能包含账号、查询参数或片段（本机测试除外）')
        if field == 'redirect_uri' and parsed.path != '/api/auth/keycloak/callback':
            raise ValueError('登录回调地址的路径须为 /api/auth/keycloak/callback')
    if require_complete and not value['client_id']:
        raise ValueError('请填写客户端标识')
    if require_complete and value['client_type'] == 'confidential' and not value['secret']:
        raise ValueError('机密客户端须填写客户端密钥')
    return value


def prepare(body):
    if not isinstance(body, dict):
        raise ValueError('配置内容格式不正确')
    current = load()
    value = dict(current)
    if 'enabled' in body:
        if not isinstance(body['enabled'], bool):
            raise ValueError('启用状态格式不正确')
        value['enabled'] = body['enabled']
    for field in FIELDS:
        if field in body:
            if not isinstance(body[field], str) or len(body[field]) > (4096 if field == 'secret' else 2048):
                raise ValueError('配置字段格式不正确或过长')
            # An empty secret means preserve the saved one, just like SMTP settings.
            if field != 'secret' or body[field]:
                value[field] = body[field].strip() if field != 'secret' else body[field]
    value['issuer'] = value['issuer'].rstrip('/')
    if body.get('clear_secret') is True or value['client_type'] == 'public':
        value['secret'] = ''
    return validate(value, require_complete=value['enabled'])


def public(value=None):
    value = load() if value is None else value
    return {k: v for k, v in value.items() if k != 'secret'} | {'has_secret': bool(value['secret'])}


def save(body):
    value = prepare(body)
    with db.write() as conn:
        db.set_setting(conn, KEY, value)
        # A change of client or enable state invalidates unfinished login transactions.
        conn.execute('DELETE FROM keycloak_flows')
    return public(value)
