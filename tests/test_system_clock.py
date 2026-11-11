from datetime import datetime
from unittest.mock import patch

import test_schedule
from server import schedule_store, system_clock


class ClockFixture(test_schedule.ScheduleTest):
    """Shared fixture only; the schedule cases are not inherited below."""
    test_lifecycle_and_repeat_safe_attendance = None
    test_reject_wrong_person_and_time = None
    test_invalid_shift = None
    test_member_only_sees_own_shifts_and_attendance = None
    test_location_is_optional = None
    test_duty_rules_control_check_in_window = None
    test_special_shifts_are_listed_and_edited = None
    test_check_in_chains_only_pairs_1_2_and_3_4 = None
    test_pairs_use_day_order_not_only_this_person_shifts = None
    test_chained_shifts_stay_on_time_while_first_keeps_lateness = None
    test_overnight_pair_does_not_chain_into_next_day = None
    test_overnight_appears_and_signs_in_after_midnight = None


class TestSystemClock(ClockFixture):
    def setUp(self):
        super().setUp()
        # The offset is process-global; each test starts from automatic time.
        system_clock._offset = None
        system_clock._loaded = False

    def tearDown(self):
        system_clock._offset = None
        system_clock._loaded = False
        super().tearDown()

    def test_automatic_mode_uses_real_time(self):
        with patch.object(schedule_store, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 2, 9, 0, tzinfo=schedule_store.TZ)
            state = system_clock.save(automatic=True)
            assert state['automatic'] is True
            assert system_clock.now() == datetime(2026, 11, 2, 9, 0, tzinfo=schedule_store.TZ)

    def test_manual_mode_starts_at_hh_mm_00_and_ticks(self):
        with patch.object(schedule_store, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 2, 9, 0, 0, tzinfo=schedule_store.TZ)
            state = system_clock.save(automatic=False, hour=14, minute=25)
            assert state['automatic'] is False
            assert state['hour'] == 14 and state['minute'] == 25
            now = system_clock.now()
            assert now.hour == 14 and now.minute == 25 and now.second == 0
            # Five real seconds later the pinned clock is 14:25:05.
            clock.now.return_value = datetime(2026, 11, 2, 9, 0, 5, tzinfo=schedule_store.TZ)
            assert system_clock.now().second == 5
            assert system_clock.now().minute == 25

    def test_manual_time_can_be_read_back_after_reload(self):
        with patch.object(schedule_store, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 2, 8, 0, tzinfo=schedule_store.TZ)
            system_clock.save(automatic=False, hour=22, minute=45)
            # Simulate a restart: drop the cached offset and reload from storage.
            system_clock._offset = None
            system_clock._loaded = False
            state = system_clock.load()
            assert state['automatic'] is False
            assert state['hour'] == 22 and state['minute'] == 45

    def test_restoring_automatic_clears_the_offset(self):
        with patch.object(schedule_store, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 2, 9, 0, tzinfo=schedule_store.TZ)
            system_clock.save(automatic=False, hour=3, minute=0)
            assert system_clock.now().hour == 3
            system_clock.save(automatic=True)
            assert system_clock.now().hour == 9

    def test_invalid_manual_time_is_rejected(self):
        with self.assertRaises(ValueError):
            system_clock.save(automatic=False, hour=24, minute=0)
        with self.assertRaises(ValueError):
            system_clock.save(automatic=False, hour=0, minute=60)

    def test_check_in_follows_the_manual_clock(self):
        sid = schedule_store.save(dict(day='2026-11-02', start='09:00', end='12:00', members=['张三']))
        with patch.object(schedule_store, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 2, 7, 0, tzinfo=schedule_store.TZ)
            system_clock.save(automatic=False, hour=9, minute=30)
            # The real wall clock is 07:00 but the system clock reads 09:30.
            stamp = schedule_store.check_in(sid, '张三')
            assert stamp.startswith('2026-11-02T09:30')

    def test_manual_mode_pins_a_date_too(self):
        with patch.object(schedule_store, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 2, 9, 0, tzinfo=schedule_store.TZ)
            state = system_clock.save(automatic=False, hour=8, minute=15, day='2026-12-24')
            assert state['date'] == '2026-12-24'
            now = system_clock.now()
            assert (now.year, now.month, now.day) == (2026, 12, 24)
            assert (now.hour, now.minute) == (8, 15)
            # Real time still advances the pinned clock across the day boundary.
            clock.now.return_value = datetime(2026, 11, 2, 9, 0, 5, tzinfo=schedule_store.TZ)
            assert system_clock.now().second == 5

    def test_pinned_date_changes_which_shifts_count(self):
        from server import duty_settings
        duty_settings.save({'early_minutes': 10, 'late_minutes': 15, 'absent_minutes': 30})
        with patch.object(schedule_store, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 2, 7, 0, tzinfo=schedule_store.TZ)
            schedule_store.save(dict(day='2026-11-02', start='09:00', end='10:00', members=['张三']))
            system_clock.save(automatic=False, hour=8, minute=55, day='2026-11-02')
            assert schedule_store.list_day('2026-11-02', '张三')[0]['members'][0]['status'] == 'pending'
            # While the shift runs, an unchecked member stays neutral.
            system_clock.save(automatic=False, hour=9, minute=45, day='2026-11-02')
            assert schedule_store.list_day('2026-11-02', '张三')[0]['members'][0]['status'] == 'pending'
            # A later day means the shift has already finished for the clock.
            system_clock.save(automatic=False, hour=8, minute=0, day='2026-11-30')
            assert schedule_store.list_day('2026-11-02', '张三')[0]['members'][0]['status'] == 'absent'

    def test_invalid_pinned_date_is_rejected(self):
        with self.assertRaises(ValueError):
            system_clock.save(automatic=False, hour=9, minute=0, day='2026-13-01')

    def test_first_shift_opens_at_0815_and_accepts_0818_with_fifteen_minutes_early(self):
        from server import duty_settings
        duty_settings.save({'early_minutes': 15, 'late_minutes': 10, 'absent_minutes': 30})
        sid = schedule_store.save(dict(day='2026-11-10', start='08:30', end='10:15', members=['张三']))
        with patch.object(schedule_store, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 10, 20, 0, tzinfo=schedule_store.TZ)
            system_clock.save(automatic=False, hour=8, minute=14, day='2026-11-10')
            with self.assertRaises(ValueError):
                schedule_store.check_in(sid, '张三')
            system_clock.save(automatic=False, hour=8, minute=18, day='2026-11-10')
            self.assertTrue(schedule_store.check_in(sid, '张三').startswith('2026-11-10T08:18:'))
