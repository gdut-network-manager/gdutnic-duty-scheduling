import asyncio
from datetime import datetime
from unittest.mock import patch
from fastapi.testclient import TestClient
from test_permissions import PermissionFixture
from server import app as api, admin_store, attendance_live, db, member_store, permissions, schedule_store, system_clock


class AttendanceLiveTest(PermissionFixture):
    def setUp(self):
        super().setUp()
        member_store.create('alice','password123')
        permissions.set_permissions('alice',['duty','overview'])
        admin_store.remember_setup('alice')

    def test_publication_wakes_all_waiting_clients(self):
        before = attendance_live.revision()
        sid = schedule_store.save(dict(day='2026-11-10',start='08:30',end='10:15',members=['alice']))
        async def scenario():
            tasks = [asyncio.create_task(attendance_live.wait(before,timeout=1)) for _ in range(3)]
            await asyncio.sleep(.01)
            with patch.object(system_clock,'now',return_value=datetime(2026,11,10,8,35,tzinfo=schedule_store.TZ)):
                schedule_store.check_in(sid,'alice')
            result = await asyncio.gather(*tasks)
            self.assertEqual(len(set(result)),1)
            self.assertNotEqual(result[0],before)
        asyncio.run(scenario())

    def test_failed_and_repeat_checkins_do_not_send_spurious_changes(self):
        sid = schedule_store.save(dict(day='2026-11-10',start='08:30',end='10:15',members=['alice']))
        before = attendance_live.revision()
        with self.assertRaises(ValueError):
            schedule_store.check_in_with_chain(sid,'alice',datetime(2026,11,10,7,0,tzinfo=schedule_store.TZ))
        self.assertEqual(attendance_live.revision(),before)
        schedule_store.check_in_with_chain(sid,'alice',datetime(2026,11,10,8,35,tzinfo=schedule_store.TZ))
        after = attendance_live.revision()
        self.assertNotEqual(after,before)
        schedule_store.check_in_with_chain(sid,'alice',datetime(2026,11,10,8,36,tzinfo=schedule_store.TZ))
        self.assertEqual(attendance_live.revision(),after)

    def test_revision_is_transactional_and_detects_another_process(self):
        before = attendance_live.revision()
        with self.assertRaises(ValueError):
            with db.write() as conn:
                attendance_live.publish(conn)
                raise ValueError('rollback')
        self.assertEqual(attendance_live.revision(),before)
        with db.write() as conn:
            db.set_setting(conn, attendance_live.KEY, 'another-process')
        self.assertEqual(asyncio.run(attendance_live.wait(before,timeout=.01)),'another-process')
        self.assertEqual(asyncio.run(attendance_live.wait('another-process',timeout=.01)),'another-process')

    def test_updates_are_authenticated_scoped_and_never_replace_login_cookies(self):
        anonymous = TestClient(api.app)
        self.assertEqual(anonymous.get('/api/attendance/live').status_code,401)
        member = TestClient(api.app)
        member.post('/api/member/login',json={'username':'alice','password':'password123'})
        response = member.get('/api/attendance/live')
        self.assertEqual(response.status_code,200)
        self.assertEqual(set(response.json()),{'revision'})
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertNotIn('set-cookie',response.headers)
        self.assertEqual(member.get('/api/attendance/live?role=admin').status_code,401)
        member.get('/' + admin_store.path())
        member.post('/api/admin/login',json={'username':'alice','password':'password123'})
        self.assertEqual(member.get('/api/attendance/live?role=admin').status_code,200)
        permissions.set_permissions('alice',['checkin_manage'])
        self.assertEqual(member.get('/api/attendance/live?role=admin').status_code,403)
        self.assertEqual(member.get('/api/attendance/live?role=invalid').status_code,400)
