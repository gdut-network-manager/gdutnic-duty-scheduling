"""Runtime data must initialize safely and stay outside the commit surface."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from server.init_data import ROOT, initialize


class DataInitializationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copy2(ROOT / '.env.example', self.root / '.env.example')

    def test_empty_checkout_and_repeat_preserve_configuration_and_database(self):
        data = initialize(self.root)
        for name in ('mysql', 'mysql-config', 'imports', 'backups', 'logs', 'certs'):
            self.assertTrue((data / name).is_dir())
        from dotenv import dotenv_values
        env = dotenv_values(data / '.env')
        self.assertEqual(len(env['MYSQL_PASSWORD']), 64)
        self.assertNotEqual(env['MYSQL_PASSWORD'], env['MYSQL_ROOT_PASSWORD'])
        original = (data / '.env').read_bytes()
        (data / 'app.sqlite').write_bytes(b'existing database')
        initialize(self.root)
        self.assertEqual((data / '.env').read_bytes(), original)
        self.assertEqual((data / 'app.sqlite').read_bytes(), b'existing database')
        if os.name != 'nt':
            self.assertEqual((data / '.env').stat().st_mode & 0o777, 0o600)

    def test_existing_root_configuration_is_moved_without_changing_secrets(self):
        old = self.root / '.env'
        old.write_text('MYSQL_PASSWORD=existing-secret\n')
        data = initialize(self.root)
        self.assertFalse(old.exists())
        self.assertEqual((data / '.env').read_text(), 'MYSQL_PASSWORD=existing-secret\n')

    def test_conflicting_configurations_stop_without_overwriting(self):
        data = initialize(self.root)
        original = (data / '.env').read_bytes()
        (self.root / '.env').write_text('MYSQL_PASSWORD=different-secret\n')
        with self.assertRaises(RuntimeError):
            initialize(self.root)
        self.assertEqual((data / '.env').read_bytes(), original)
        self.assertTrue((self.root / '.env').exists())

    def test_new_checkout_can_start_register_and_restart_with_sqlite(self):
        for folder in ('server', 'sql', 'web', 'icon'):
            shutil.copytree(ROOT / folder, self.root / folder, ignore=shutil.ignore_patterns('__pycache__'))
        script = '''
from fastapi.testclient import TestClient
from server.app import app
from server import db, admin_store, member_store, permissions
from pathlib import Path
assert db.DATA == Path.cwd() / 'data'
with TestClient(app) as client:
    response = client.get('/', follow_redirects=False)
    if not db.get_setting(db.connect(), 'bootstrap_test_done'):
        assert response.status_code == 302
        assert client.get(response.headers['location']).status_code == 200
        # The first account is the 原始管理员; the operator chooses its name here.
        result = client.post('/api/admin/register', json={'username':'部署负责人',
                                                          'password':'test-password-123'})
        assert result.status_code == 200, result.text
        assert result.json()['username'] == '部署负责人'
        member_store.create('重启验证会员')
        assert permissions.permissions_of('重启验证会员') == set(permissions.DEFAULT_PERMISSIONS)
        assert client.post('/api/member/login', json={'username':'重启验证会员',
                                                     'password':'gg2017'}).status_code == 200
        changed = client.post('/api/member/password', json={'old_password':'gg2017',
                                                           'new_password':'changed-member-password'})
        assert changed.status_code == 200, changed.text
        with db.write() as conn:
            db.set_setting(conn, 'bootstrap_test_done', True)
    else:
        assert response.status_code == 200
        assert client.get('/' + admin_store.path()).status_code == 200
        assert client.post('/api/admin/login', json={'username':'部署负责人',
                                                     'password':'test-password-123'}).status_code == 200
        # A fresh process and the complete startup must preserve the changed hash,
        # including when roster provisioning encounters the same member again.
        assert member_store.create_missing(['重启验证会员'])['created'] == 0
        assert client.post('/api/member/login', json={'username':'重启验证会员',
                                                     'password':'gg2017'}).status_code == 401
        assert client.post('/api/member/login', json={'username':'重启验证会员',
                                                     'password':'changed-member-password'}).status_code == 200
    # The root account never shows up in the account list, even to itself.
    listed = client.get('/api/admin/members').json()['items']
    assert all(item['username'] != '部署负责人' for item in listed), listed
    assert client.get('/data/.env').status_code == 404
    assert client.get('/data/app.sqlite').status_code == 404
    assert client.get('/data/root_admin.json').status_code == 404
assert db.DB_PATH.is_file()
assert (db.DATA / 'session_secret.txt').is_file()
assert (db.DATA / 'root_admin.json').is_file()
assert (db.DATA / 'root_admin.json').stat().st_mode & 0o777 == 0o600
'''
        env = {**os.environ, 'DB_BACKEND': 'sqlite', 'PYTHONPATH': str(self.root), 'PYTHONDONTWRITEBYTECODE': '1'}
        env.pop('SESSION_SECRET', None)
        for _ in range(2):
            result = subprocess.run([sys.executable, '-c', script], cwd=self.root, env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / '.env').exists())
        self.assertFalse(list(self.root.glob('*.sqlite*')))

    def test_git_ignores_private_files_but_keeps_schema_and_example(self):
        private = ['data/.env', 'data/mysql/mysql.ibd', 'data/backups/export.sql',
                   'export.sql', 'accidental.db', 'accidental.sqlite3-wal', 'people.csv', 'private.key']
        for name in private + ['sql/mysql_schema.sql', '.env.example']:
            result = subprocess.run(['git', 'check-ignore', '--no-index', '-q', name], cwd=ROOT)
            self.assertEqual(result.returncode, 0 if name in private else 1, name)
