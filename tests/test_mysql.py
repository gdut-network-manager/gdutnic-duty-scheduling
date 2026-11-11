"""Opt-in integration tests against a disposable database on the local Docker MySQL.
Run: RUN_MYSQL_TESTS=1 .venv/bin/python -m unittest discover -s tests -p test_mysql.py -v
"""
from contextlib import closing
import os
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from server import db


@unittest.skipUnless(os.environ.get('RUN_MYSQL_TESTS') == '1', 'set RUN_MYSQL_TESTS=1 for real MySQL tests')
class MysqlIntegrationTest(unittest.TestCase):
    def setUp(self):
        import pymysql
        self.temp = tempfile.TemporaryDirectory()
        self.name = 'gdutnic_test_' + uuid.uuid4().hex
        self.root = pymysql.connect(host=os.getenv('MYSQL_HOST','127.0.0.1'), port=int(os.getenv('MYSQL_PORT','3307')),
                                    user='root', password=os.environ['MYSQL_ROOT_PASSWORD'], autocommit=True)
        with self.root.cursor() as c: c.execute(f'CREATE DATABASE `{self.name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin')
        self.old = db._conn, db.DATA
        db._conn = None; db.DATA = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'DB_BACKEND':'mysql', 'MYSQL_DATABASE':self.name,
                                          'MYSQL_USER':'root', 'MYSQL_PASSWORD':os.environ['MYSQL_ROOT_PASSWORD']})
        self.env.start()

    def tearDown(self):
        if db._conn and db._conn.raw.open: db._conn.close()
        db._conn, db.DATA = self.old
        self.env.stop()
        with self.root.cursor() as c: c.execute(f'DROP DATABASE `{self.name}`')
        self.root.close(); self.temp.cleanup()

    def test_push_config_delivery_claims_and_aggregation_on_mysql(self):
        from server import push_reports as push, admin_store, permissions, member_store, schedule_store
        from copy import deepcopy
        permissions.ensure_defaults(); member_store.create('推送成员')
        sid=schedule_store.save({'day':'2026-11-02','start':'09:00','end':'12:00','members':['推送成员']})
        with db.write() as conn:
            conn.execute("UPDATE attendance SET status_override='late' WHERE shift_id=?",(sid,))
        admin_store.save_smtp({'enabled':True,'host':'smtp.example.com','from_address':'bot@example.com'})
        config=deepcopy(push.DEFAULTS); config.update(enabled=True,recipients=['one@example.com'])
        push.save(config,now=datetime(2026,11,6,12,tzinfo=push.TZ))
        with patch.object(push.mailer,'send') as send:
            push.run_due(datetime(2026,11,8,9,tzinfo=push.TZ))
            push.run_due(datetime(2026,11,8,9,1,tzinfo=push.TZ))
            send.assert_called_once()
            self.assertIn('推送成员',send.call_args.args[2])
        self.assertEqual(push.status()['deliveries'][0]['state'],'sent')
        self.assertEqual(push.load()['next_run'],'2026-11-15T09:00:00+08:00')

    def test_permission_groups_update_shared_access_and_revoke_old_sessions(self):
        from fastapi.testclient import TestClient
        from server.app import app
        from server import admin_store, member_store, permissions
        member_store.create('operator', 'password123')
        permissions.set_permissions('operator', permissions.ALL_PERMISSIONS)
        admin_store.remember_setup('operator')
        owner = TestClient(app)
        owner.get('/' + admin_store.path())
        self.assertEqual(owner.post('/api/admin/login', json={'username':'operator','password':'password123'}).status_code, 200)
        group = owner.post('/api/admin/permission-groups', json={'name':'日志组','permissions':['logs']}).json()
        member_store.create('reader', 'password123')
        for _ in range(2):
            self.assertEqual(owner.put('/api/admin/members/permissions', json={'username':'reader','group_id':group['id']}).status_code, 200)
        groups = owner.get('/api/admin/permission-groups').json()['items']
        self.assertEqual(next(g for g in groups if g['id']==group['id'])['member_count'], 1)
        reader = TestClient(app)
        reader.get('/' + admin_store.path())
        self.assertEqual(reader.post('/api/admin/login', json={'username':'reader','password':'password123'}).status_code, 200)
        self.assertEqual(reader.get('/api/admin/logs').status_code, 200)
        self.assertEqual(reader.get('/api/admin/overview').status_code, 403)
        self.assertEqual(owner.put('/api/admin/permission-groups/'+group['id'], json={'name':'概览组','permissions':['overview']}).status_code, 200)
        self.assertEqual(reader.get('/api/admin/logs').status_code, 403)
        self.assertEqual(reader.get('/api/admin/overview').status_code, 200)
        self.assertEqual(owner.delete('/api/admin/permission-groups/'+group['id']).status_code, 200)
        self.assertEqual(reader.get('/api/admin/overview').status_code, 401)
        self.assertEqual(permissions.permissions_of('reader'), set(permissions.DEFAULT_PERMISSIONS))

    def test_real_mysql_stores_and_transactions(self):
        from server import admin_store, member_store, schedule_store, weekly_store, chat_store
        from server import permissions
        admin_store.load()
        member_store.create('管理员', 'password123')
        permissions.set_permissions('管理员', permissions.ADMIN_DEFAULTS)
        admin_store.remember_setup('管理员')
        # Administrators now live in the account table and sign in through it.
        self.assertTrue(member_store.verify('管理员', 'password123'))
        self.assertTrue(permissions.is_admin('管理员'))
        member_store.create('第二管理员', 'password456')
        permissions.set_permissions('第二管理员', ['user', 'schedule'])
        self.assertFalse(permissions.has('第二管理员', 'accounts_protected'))
        member_store.delete('第二管理员')
        member_store.create_missing(['张三','李四'])
        self.assertTrue(member_store.verify('张三','gg2017'))
        roster=dict(location='网络中心',needs_review=False, slots=[dict(start=a,end=b,days=[['张三','李四'] for _ in range(7)])
                     for a,b in [('08:00','10:00'),('10:00','12:00'),('14:00','16:00'),('16:00','18:00')]])
        weekly_store.save(roster)
        self.assertEqual(weekly_store.publish('2026-11-02')['created'], 28)
        sid=schedule_store.list_day('2026-11-02')[0]['id']
        with patch.object(schedule_store,'datetime',wraps=datetime) as clock:
            clock.now.return_value=datetime(2026,11,2,9,0,tzinfo=schedule_store.TZ)
            first=schedule_store.check_in(sid,'张三')
            self.assertEqual(schedule_store.check_in(sid,'张三'),first)
        # Only the two checked-in shifts survive regeneration; the rest are rebuilt.
        self.assertEqual(weekly_store.publish('2026-11-02'), dict(created=26,skipped=2))
        self.assertIn(sid, [s['id'] for s in schedule_store.list_day('2026-11-02')])
        schedule_store.delete(sid)
        with db.read() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM attendance WHERE shift_id=?',(sid,)).fetchone()['n'],0)
        with self.assertRaises(RuntimeError):
            with db.write() as c:
                db.set_setting(c,'rollback-test',1)
                raise RuntimeError('rollback')
        with db.read() as c: self.assertIsNone(db.get_setting(c,'rollback-test'))
        chat_store.save_visitor_chats('test-visitor',[dict(id='chat',title='测试',updatedAt=1,messages=[dict(role='user',content='你好')])],'chat')
        self.assertEqual(chat_store.load_visitor_chats('test-visitor')['currentId'],'chat')
        self.assertEqual(schedule_store.clear_all(), {'deleted': 27, 'archived': 1})
        with db.read() as c:
            for table in ('shifts', 'attendance', 'weekly_instances'):
                self.assertEqual(c.execute(f'SELECT COUNT(*) AS n FROM {table}').fetchone()['n'], 0)
        self.assertTrue(member_store.verify('张三', 'gg2017'))
        with patch.object(weekly_store, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 3, 9, 0, tzinfo=schedule_store.TZ)
            self.assertEqual(weekly_store.publish_until('2026-11-04')['created'], 8)
        self.assertEqual(schedule_store.list_day('2026-11-02'), [])
        self.assertEqual(len(schedule_store.list_day('2026-11-04')), 4)

    def test_reconnect_before_next_transaction(self):
        with db.write() as c:
            db.set_setting(c, 'connection-test', '保留数据')
        # Simulate a connection closed by an idle timeout or server restart.
        db._conn.raw.close()
        with db.read() as c:
            self.assertEqual(db.get_setting(c, 'connection-test'), '保留数据')
        db._conn.raw.close()
        with db.write() as c:
            db.set_setting(c, 'connection-test', '恢复写入')
        with db.read() as c:
            self.assertEqual(db.get_setting(c, 'connection-test'), '恢复写入')

    def test_existing_database_gains_special_column(self):
        with db.read() as conn:
            conn.execute("ALTER TABLE shifts DROP COLUMN special")
        db._conn.close(); db._conn = None
        with db.read() as conn:
            columns = {row['COLUMN_NAME'] for row in conn.execute(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()"
                " AND TABLE_NAME='shifts'").fetchall()}
        self.assertIn('special', columns)
        from server import schedule_store
        sid = schedule_store.save(dict(day='2026-11-20', start='20:00', end='22:00', members=['张三'], special=True))
        self.assertEqual(schedule_store.list_special()[0]['id'], sid)

    def test_add_and_delete_member_accounts(self):
        from server import member_store
        member_store.create('王五')
        self.assertTrue(member_store.verify('王五', 'gg2017'))
        with self.assertRaises(ValueError):
            member_store.create('王五')
        member_store.delete('王五')
        self.assertFalse(member_store.verify('王五', 'gg2017'))
        with self.assertRaises(LookupError):
            member_store.delete('王五')

    def test_weekly_save_registers_only_new_roster_accounts(self):
        from server import member_store, weekly_store
        member_store.create_missing(['老人员'])
        with db.read() as conn:
            before = {r['username'] for r in conn.execute('SELECT username FROM member_accounts')}
        roster = dict(needs_review=False, slots=[
            dict(start=a, end=b, days=[['老人员', '新甲', '新乙'] for _ in range(7)])
            for a, b in [('08:00','10:00'),('10:00','12:00'),('14:00','16:00'),('16:00','18:00')]])
        weekly_store.save(roster)
        created = member_store.create_missing(
            {name for slot in weekly_store.load()['slots'] for names in slot['days'] for name in names})
        self.assertEqual(created['created'], 2)
        with db.read() as conn:
            after = {r['username'] for r in conn.execute('SELECT username FROM member_accounts')}
        self.assertEqual(after - before, {'新甲', '新乙'})
        self.assertTrue(member_store.verify('新甲', 'gg2017'))
        self.assertTrue(member_store.verify('老人员', 'gg2017'))

    def test_api_identity_and_admin_permissions(self):
        from fastapi.testclient import TestClient
        from server.app import app
        from server import admin_store,member_store,schedule_store
        from server import permissions
        member_store.create_missing(['张三','李四'])
        member_store.create('管理员', 'password123')
        permissions.set_permissions('管理员', permissions.ADMIN_DEFAULTS)
        admin_store.remember_setup('管理员')
        c=TestClient(app)
        self.assertEqual(c.get('/api/schedules?day=2026-11-02').status_code,401)
        self.assertEqual(c.post('/api/member/login',json={'username':'张三','password':'gg2017'}).status_code,200)
        self.assertEqual(c.get('/api/admin/members').status_code,401)
        self.assertEqual(c.post('/api/admin/schedules',json={}).status_code,401)
        sid=schedule_store.save(dict(day='2026-11-02',start='08:00',end='12:00',location='中心',members=['张三','李四']))
        schedule_store.save(dict(day='2026-11-02',start='14:00',end='16:00',members=['李四']))
        own = c.get('/api/schedules?day=2026-11-02').json()['items']
        self.assertEqual([s['id'] for s in own], [sid])
        self.assertEqual([{k: v for k, v in m.items() if k != 'status'} for m in own[0]['members']],
                         [{'name': '张三', 'checked_at': None}, {'name': '李四', 'checked_at': None}])
        self.assertEqual(c.delete('/api/admin/schedules').status_code, 401)
        self.assertEqual(c.post('/api/admin/special-schedules', json=dict(day='2026-11-20',start='20:00',end='22:00',members=['李四'])).status_code, 401)
        self.assertEqual(c.get('/api/special-schedules').status_code, 200)
        self.assertEqual(c.get('/api/special-schedules').json()['items'], [])
        with patch.object(schedule_store,'datetime',wraps=datetime) as clock:
            clock.now.return_value=datetime(2026,11,2,9,0,tzinfo=schedule_store.TZ)
            self.assertEqual(c.post(f'/api/schedules/{sid}/check-in').status_code,200)
        members={r['name']:r['checked_at'] for r in schedule_store.list_day('2026-11-02')[0]['members']}
        self.assertIsNotNone(members['张三']); self.assertIsNone(members['李四'])
        admin = TestClient(app)
        self.assertEqual(admin.get('/' + admin_store.path()).status_code, 200)
        self.assertEqual(admin.post('/api/admin/login', json={'username':'管理员','password':'password123'}).status_code, 200)
        self.assertEqual(admin.delete('/api/admin/members', params={'username':'张三'}).status_code, 200)
        with db.read() as conn:
            self.assertFalse(conn.execute("SELECT 1 FROM member_accounts WHERE username='张三'").fetchone())
        self.assertFalse(c.get('/api/member/status').json()['logged_in'])
        self.assertEqual(admin.post('/api/admin/members', json={'username':'王五'}).status_code, 200)
        self.assertTrue(member_store.verify('王五', 'gg2017'))
        self.assertEqual(admin.post('/api/admin/special-schedules', json=dict(day='2026-11-20',start='20:00',end='22:00',members=['李四','王五'])).status_code, 200)
        specials = admin.get('/api/special-schedules').json()['items']
        self.assertEqual([s['day'] for s in specials], ['2026-11-20'])
        self.assertEqual(len(specials[0]['members']), 2)
        self.assertEqual(admin.put('/api/admin/rules', json={'early_minutes':20,'late_minutes':10,'absent_minutes':45}).status_code, 200)
        self.assertEqual(admin.get('/api/admin/rules').json(), {'early_minutes':20,'late_minutes':10,'absent_minutes':45})
        saved = admin.put('/api/admin/weekly-roster', json=dict(needs_review=False, slots=[
            dict(start=a, end=b, days=[['张三', '新人员甲', '新人员乙'] for _ in range(7)])
            for a, b in [('08:00','10:00'),('10:00','12:00'),('14:00','16:00'),('16:00','18:00')]]))
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()['members_created'], 3)
        self.assertTrue(member_store.verify('新人员甲', 'gg2017'))
        self.assertTrue(member_store.verify('张三', 'gg2017'))
        self.assertEqual(c.post('/api/member/login',json={'username':'李四','password':'gg2017'}).status_code,200)
        with patch.object(schedule_store,'datetime',wraps=datetime) as clock:
            clock.now.return_value=datetime(2026,11,2,9,0,tzinfo=schedule_store.TZ)
            self.assertEqual(c.post(f'/api/schedules/{sid}/check-in').status_code,200)
        self.assertEqual(c.post('/api/member/password',json={'old_password':'gg2017','new_password':'changed123'}).status_code,200)
        member_store.update('李四',reset=True)
        self.assertFalse(c.get('/api/member/status').json()['logged_in'])
        self.assertEqual(c.post(f'/api/schedules/{sid}/check-in').status_code,401)

    def test_migration_values_repeat_refusal_and_rollback(self):
        import sqlite3
        from server.migrate_mysql import migrate
        source=Path(self.temp.name)/'old.sqlite'
        with closing(sqlite3.connect(source)) as raw, raw as c:
            c.executescript(db.SCHEMA)
            c.execute('INSERT INTO users VALUES(?,?)',('旧用户','hash-value'))
            c.execute('INSERT INTO push_deliveries VALUES(?,?,?,?,?,?)',('digest-id','2026-10-31','one@example.com','sent','2026-11-01T09:00:00+08:00',''))
            c.execute('INSERT INTO shifts(day,start,end,location,note) VALUES(?,?,?,?,?)',('2026-11-02','08:00','10:00','中心','测试'))
            c.execute('INSERT INTO attendance(shift_id,name,checked_at) VALUES(?,?,?)',(1,'张三','2026-11-02T08:30:00+08:00'))
            c.execute('INSERT INTO attendance(shift_id,name,checked_at) VALUES(?,?,?)',(1,'李四',None))
        counts,backup=migrate(source)
        self.assertEqual(counts['users'],1);self.assertTrue(backup.exists())
        self.assertEqual(counts['push_deliveries'],1)
        with db.read() as c:
            self.assertEqual(c.execute('SELECT checked_at FROM attendance WHERE name=?',('张三',)).fetchone()['checked_at'],'2026-11-02T08:30:00+08:00')
        with self.assertRaises(ValueError): migrate(source)
        with db.read() as c: self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM users').fetchone()['n'],1)

    def test_mysql_publication_failure_rolls_back_all_days(self):
        import pymysql
        from server import schedule_store, weekly_store
        roster=dict(location='中心',needs_review=False,slots=[dict(start=a,end=b,days=[['张三'] for _ in range(7)])
                    for a,b in [('08:00','10:00'),('10:00','12:00'),('14:00','16:00'),('16:00','18:00')]])
        weekly_store.save(roster)
        with db.read() as c:
            c.execute("CREATE TRIGGER reject_wednesday BEFORE INSERT ON shifts FOR EACH ROW BEGIN IF NEW.day='2026-11-04' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='test rollback'; END IF; END")
        with self.assertRaises(pymysql.MySQLError): weekly_store.publish('2026-11-02')
        self.assertEqual(schedule_store.list_day('2026-11-02'),[])
        with db.read() as c: self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM weekly_instances').fetchone()['n'],0)

    def test_migration_failure_preserves_empty_target(self):
        import pymysql,sqlite3
        from server.migrate_mysql import migrate
        source=Path(self.temp.name)/'invalid.sqlite'
        with closing(sqlite3.connect(source)) as raw, raw as c:
            c.executescript(db.SCHEMA)
            c.execute('INSERT INTO users VALUES(?,?)',('旧用户','hash-value'))
            c.execute('INSERT INTO push_deliveries VALUES(?,?,?,?,?,?)',('digest-id','2026-10-31','one@example.com','sent','2026-11-01T09:00:00+08:00',''))
            c.execute('INSERT INTO attendance(shift_id,name,checked_at) VALUES(?,?,NULL)',(999,'张三'))
        with self.assertRaises(pymysql.IntegrityError): migrate(source)
        with db.read() as c: self.assertEqual(c.execute('SELECT COUNT(*) AS n FROM users').fetchone()['n'],0)

    def test_mysql_heartbeat_is_independent_of_idle_and_uses_role_limits(self):
        from server import session_policy
        with patch.object(session_policy.time, 'time', return_value=2000000000.0) as clock:
            limits = session_policy.save({'member_minutes':120,'admin_minutes':30,
                'member_response_seconds':45,'admin_response_seconds':120})
            member = session_policy.create('member','heartbeat-member')
            admin = session_policy.create('admin','heartbeat-admin')
            original = session_policy.state(member,'member')['expires_at']
            clock.return_value += 30
            self.assertTrue(session_policy.heartbeat(member,'member','heartbeat-member'))
            self.assertEqual(session_policy.state(member,'member')['expires_at'],original)
            self.assertEqual(limits['admin_response_seconds'],120)
            clock.return_value += 45
            self.assertEqual(session_policy.reap_unresponsive(),1)
            self.assertIsNone(session_policy.state(member,'member'))
            self.assertIsNotNone(session_policy.state(admin,'admin'))
