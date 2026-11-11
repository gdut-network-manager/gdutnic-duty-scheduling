import tempfile
import os
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from server import db, schedule_store as store


class ScheduleTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"DB_BACKEND": "sqlite"})
        self.env.start()
        self.temp = tempfile.TemporaryDirectory()
        self.old = (db.DATA, db.DB_PATH, db._conn)
        db.DATA = Path(self.temp.name)
        db.DB_PATH = db.DATA / 'test.sqlite'
        db._conn = None
        self.now = datetime(2026, 11, 2, 10, 30, tzinfo=store.TZ)
        self.body = dict(day='2026-11-02', start='09:00', end='12:00', location='值班室', members=['张三', '李四'], note='交接')

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db.DATA, db.DB_PATH, db._conn = self.old
        self.temp.cleanup()
        self.env.stop()

    def check(self, sid, name, now=None):
        with patch.object(store, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = now or self.now
            return store.check_in(sid, name)

    def test_lifecycle_and_repeat_safe_attendance(self):
        sid = store.save(self.body)
        first = self.check(sid, '张三')
        self.assertEqual(first, self.check(sid, '张三', self.now + timedelta(minutes=5)))
        self.assertEqual(store.list_day(self.body['day'])[0]['members'][0]['checked_at'], first)
        store.save({**self.body, 'location': '新值班室'}, sid)
        with self.assertRaises(ValueError):
            store.save({**self.body, 'members': ['李四']}, sid)
        store.delete(sid)
        self.assertEqual(store.list_day(self.body['day']), [])
        with db.read() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) AS n FROM attendance').fetchone()['n'], 0)

    def test_reject_wrong_person_and_time(self):
        sid = store.save(self.body)
        with self.assertRaises(LookupError):
            self.check(sid, '未排班人员')
        for now in (self.now.replace(hour=8, minute=40), self.now.replace(hour=12, minute=0), self.now + timedelta(days=1)):
            with self.assertRaises(ValueError):
                self.check(sid, '张三', now)
        # Early check-in opens ten minutes before the shift by default.
        self.check(sid, '张三', self.now.replace(hour=8, minute=50))

    def test_invalid_shift(self):
        for change in ({'end': '09:00'}, {'day': 'invalid'}, {'members': []}, {'location': '地' * 121}):
            with self.assertRaises(ValueError):
                store.save({**self.body, **change})

    def test_member_only_sees_own_shifts_and_attendance(self):
        sid = store.save({**self.body, 'start': '22:00', 'end': '02:00'})
        store.save({**self.body, 'members': ['其他人']})
        for day in ('2026-11-02', '2026-11-03'):
            own = store.list_day(day, '张三')
            self.assertEqual([s['id'] for s in own], [sid])
            self.assertEqual([{k: v for k, v in m.items() if k != 'status'} for m in own[0]['members']],
                             [{'name': '张三', 'checked_at': None}, {'name': '李四', 'checked_at': None}])
            self.assertEqual(store.list_day(day, '未排班人员'), [])
        self.assertEqual(len(store.list_day('2026-11-02')), 2)

    def test_location_is_optional(self):
        body = {k: v for k, v in self.body.items() if k != 'location'}
        store.save(body)
        self.assertEqual(store.list_day(body['day'])[0]['location'], '')

    def test_duty_rules_control_check_in_window(self):
        from server import duty_settings
        self.assertEqual(duty_settings.load(), duty_settings.DEFAULTS)
        sid = store.save(self.body)
        duty_settings.save({'early_minutes': 30, 'late_minutes': 5, 'absent_minutes': 20})
        self.assertEqual(duty_settings.load(), {'early_minutes': 30, 'late_minutes': 5, 'absent_minutes': 20})
        self.check(sid, '张三', self.now.replace(hour=8, minute=30))
        duty_settings.save({'early_minutes': 0, 'late_minutes': 5, 'absent_minutes': 20})
        with self.assertRaises(ValueError):
            self.check(sid, '李四', self.now.replace(hour=8, minute=59))
        # Out-of-range values are clamped instead of breaking the page.
        self.assertEqual(duty_settings.save({'early_minutes': 999, 'late_minutes': -1, 'absent_minutes': 0}),
                         {'early_minutes': 120, 'late_minutes': 0, 'absent_minutes': 1})
        # Absence is measured from the shift start and must sit after the late window.
        with self.assertRaises(ValueError):
            duty_settings.save({'late_minutes': 30, 'absent_minutes': 30})
        # Legacy stored values are repaired on load rather than producing an empty window.
        with db.write() as conn:
            db.set_setting(conn, duty_settings.KEY, {'late_minutes': 40, 'absent_minutes': 30})
        self.assertEqual(duty_settings.load()['absent_minutes'], 41)

    def test_special_shifts_are_listed_and_edited(self):
        special = store.save({**self.body, 'day': '2026-11-20', 'special': True})
        plain = store.save({**self.body, 'day': '2026-11-21'})
        self.assertEqual([s['id'] for s in store.list_special()], [special])
        self.assertEqual([s['id'] for s in store.list_special(name='张三')], [special])
        self.assertEqual(store.list_special(name='其他人'), [])
        store.save({**self.body, 'day': '2026-11-22', 'members': ['张三', '李四', '王五']}, special)
        self.assertEqual(store.list_special()[0]['day'], '2026-11-22')
        self.assertEqual([m['name'] for m in store.list_special()[0]['members']], ['张三', '李四', '王五'])
        self.assertEqual(store.list_special(from_day='2026-11-22')[0]['id'], special)
        with self.assertRaises(ValueError):
            store.list_special(from_day='2026-13-01')
        store.delete(special)
        self.assertEqual(store.list_special(), [])
        self.assertEqual(store.list_day('2026-11-21')[0]['id'], plain)

    def test_check_in_chains_only_pairs_1_2_and_3_4(self):
        first = store.save({**self.body, 'start': '09:00', 'end': '10:00'})
        second = store.save({**self.body, 'start': '10:00', 'end': '11:00'})
        third = store.save({**self.body, 'start': '11:00', 'end': '12:00'})
        fourth = store.save({**self.body, 'start': '12:00', 'end': '13:00'})
        stamp, chained = store.check_in_with_chain(first, '张三', self.now.replace(hour=9, minute=10))
        self.assertEqual(chained, [second])
        self.assertEqual(stamp, '2026-11-02T09:10:00+08:00')
        rows = {r['shift_id']: r['checked_at'] for r in self._attendance()}
        self.assertEqual(rows[first], stamp)
        self.assertEqual(rows[second], stamp)
        # 2→3 is explicitly not a chain.
        self.assertIsNone(rows[third])
        self.assertIsNone(rows[fourth])
        # 3→4 is a valid pair.
        _, chained = store.check_in_with_chain(third, '张三', self.now.replace(hour=11, minute=5))
        self.assertEqual(chained, [fourth])
        rows = {r['shift_id']: r['checked_at'] for r in self._attendance()}
        self.assertEqual(rows[third], '2026-11-02T11:05:00+08:00')
        self.assertEqual(rows[fourth], '2026-11-02T11:05:00+08:00')

    def test_pairs_use_day_order_not_only_this_person_shifts(self):
        # The person is assigned to the 2nd and 3rd shifts only; 2→3 must not chain.
        store.save({**self.body, 'start': '08:00', 'end': '09:00', 'members': ['其他人']})
        second = store.save({**self.body, 'start': '09:00', 'end': '10:00', 'members': ['张三']})
        third = store.save({**self.body, 'start': '10:00', 'end': '11:00', 'members': ['张三']})
        _, chained = store.check_in_with_chain(second, '张三', self.now.replace(hour=9, minute=5))
        self.assertEqual(chained, [])
        _, chained = store.check_in_with_chain(third, '张三', self.now.replace(hour=10, minute=5))
        self.assertEqual(chained, [])

    def _attendance(self):
        with db.read() as conn:
            return conn.execute(
                'SELECT shift_id, name, checked_at FROM attendance WHERE name=? ORDER BY shift_id',
                ('张三',)).fetchall()

    def test_chained_shifts_stay_on_time_while_first_keeps_lateness(self):
        from server import duty_settings, overview
        duty_settings.save({'early_minutes': 10, 'late_minutes': 15, 'absent_minutes': 30})
        first = store.save({**self.body, 'start': '09:00', 'end': '10:00'})
        second = store.save({**self.body, 'start': '10:00', 'end': '11:00'})
        # 09:20 is late for the first shift; its partner reuses 09:20 and stays on time.
        stamp, chained = store.check_in_with_chain(first, '张三', self.now.replace(hour=9, minute=20))
        self.assertEqual(chained, [second])
        data = overview.overview('2026-11', now=self.now.replace(hour=12, minute=30))
        statuses = {s['id']: s['members'][0]['status'] for s in data['timeline']}
        self.assertEqual(statuses[first], 'late')
        self.assertEqual(statuses[second], 'normal')

    def test_overnight_pair_does_not_chain_into_next_day(self):
        night = store.save({**self.body, 'start': '22:00', 'end': '02:00', 'day': '2026-11-02'})
        morning = store.save({**self.body, 'start': '02:00', 'end': '04:00', 'day': '2026-11-03'})
        with patch.object(store, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = self.now.replace(hour=23)
            _, chained = store.check_in_with_chain(night, '张三')
        self.assertEqual(chained, [])
        with db.read() as conn:
            self.assertIsNone(conn.execute('SELECT checked_at FROM attendance WHERE shift_id=?',
                                           (morning,)).fetchone()['checked_at'])

    def test_overnight_appears_and_signs_in_after_midnight(self):
        sid = store.save({**self.body, 'start':'22:00', 'end':'02:00'})
        self.assertEqual(len(store.list_day('2026-11-02')), 1)
        self.assertEqual(len(store.list_day('2026-11-03')), 1)
        self.assertEqual(store.list_day('2026-11-04'), [])
        before = self.now.replace(hour=21)
        with self.assertRaises(ValueError): self.check(sid, '张三', before)
        midnight = self.now.replace(hour=0) + timedelta(days=1)
        self.assertIn('2026-11-03T00:30', self.check(sid, '张三', midnight))
        with self.assertRaises(ValueError): self.check(sid, '李四', midnight.replace(hour=2,minute=0))
        boundary = store.save({**self.body, 'start':'23:00', 'end':'00:00'})
        self.assertEqual(len(store.list_day('2026-11-03')), 1)


if __name__ == '__main__':
    unittest.main()
