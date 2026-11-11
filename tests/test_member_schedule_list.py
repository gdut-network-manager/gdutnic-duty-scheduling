import unittest
from fastapi.testclient import TestClient
import test_schedule
from server import member_store, overview, permissions, schedule_store


class MemberScheduleListTest(unittest.TestCase):
    setUp = test_schedule.ScheduleTest.setUp
    tearDown = test_schedule.ScheduleTest.tearDown

    def test_all_people_visible_without_attendance_fields_and_weeks_navigable(self):
        from server.app import app
        member_store.create('查看者')
        schedule_store.save(dict(day='2026-11-09', start='08:30', end='10:15',
                                 members=['甲', '乙', '丙', '丁'], note='第1班'))
        schedule_store.save(dict(day='2026-11-16', start='13:00', end='14:00',
                                 members=['其他人员'], special=True))
        client = TestClient(app)
        self.assertEqual(client.get('/api/member/schedule-list').status_code, 401)
        self.assertEqual(client.post('/api/member/login', json={'username': '查看者', 'password': 'gg2017'}).status_code, 200)
        self.assertEqual(client.get('/api/member/schedule-list').status_code, 403)
        permissions.set_permissions('查看者', ['schedule_list'])
        self.assertFalse(permissions.is_admin('查看者'))
        self.assertEqual(client.get('/api/schedules?day=2026-11-09').status_code, 403)
        response = client.get('/api/member/schedule-list?week=2026-11-09')
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        item = data['days'][0]['shifts'][0]
        self.assertEqual(set(item['members']), {'甲', '乙', '丙', '丁'})
        self.assertFalse({'checked_at', 'status', 'phase', 'absent', 'late'} & set(item))
        self.assertEqual(data['next'], '2026-11-16')
        next_week = client.get('/api/member/schedule-list?week=2026-11-16').json()
        self.assertEqual(next_week['days'][0]['shifts'][0]['special'], 1)
        self.assertEqual(client.get('/api/member/schedule-list?week=invalid').status_code, 400)
        permissions.set_permissions('查看者', ['member_field'])
        self.assertEqual(client.get('/api/member/schedule-list').status_code, 403)
