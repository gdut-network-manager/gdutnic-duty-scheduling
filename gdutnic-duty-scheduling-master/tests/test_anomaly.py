from datetime import datetime, timedelta
from unittest.mock import patch

import test_schedule
from server import overview, schedule_store


class AnomalyFixture(test_schedule.ScheduleTest):
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


class TestAnomalyStats(AnomalyFixture):
    def setUp(self):
        super().setUp()
        from server import duty_settings
        duty_settings.save({'early_minutes': 10, 'late_minutes': 15, 'absent_minutes': 30})
        self.now = datetime(2026, 11, 20, 18, 0, tzinfo=schedule_store.TZ)

    def shift(self, day, names, start='09:00', end='10:00'):
        return schedule_store.save(dict(day=day, start=start, end=end, members=names))

    def stamp(self, sid, name, at):
        with patch.object(schedule_store, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = at
            schedule_store.check_in(sid, name)

    def test_ranks_by_count_then_pinyin(self):
        # 张三 and 李四 both late once; 王五 late twice.
        a = self.shift('2026-11-10', ['张三', '李四', '王五'])
        b = self.shift('2026-11-11', ['王五'])
        self.stamp(a, '张三', datetime(2026, 11, 10, 9, 25, tzinfo=schedule_store.TZ))
        self.stamp(a, '李四', datetime(2026, 11, 10, 9, 25, tzinfo=schedule_store.TZ))
        self.stamp(a, '王五', datetime(2026, 11, 10, 9, 25, tzinfo=schedule_store.TZ))
        self.stamp(b, '王五', datetime(2026, 11, 11, 9, 25, tzinfo=schedule_store.TZ))
        data = overview.anomaly_stats(2, now=self.now)
        assert [item['name'] for item in data['late']] == ['王五', '李四', '张三']
        assert [item['count'] for item in data['late']] == [2, 1, 1]

    def test_counts_late_and_absent_separately(self):
        late = self.shift('2026-11-12', ['张三'])
        missed = self.shift('2026-11-13', ['李四'])
        self.stamp(late, '张三', datetime(2026, 11, 12, 9, 25, tzinfo=schedule_store.TZ))
        data = overview.anomaly_stats(2, now=self.now)
        assert data['late'] == [{'name': '张三', 'count': 1}]
        assert data['absent'] == [{'name': '李四', 'count': 1}]

    def test_window_is_two_or_four_weeks_ending_now(self):
        inside = self.shift('2026-11-16', ['张三'])
        outside = self.shift('2026-10-01', ['李四'])
        self.stamp(inside, '张三', datetime(2026, 11, 16, 9, 25, tzinfo=schedule_store.TZ))
        self.stamp(outside, '李四', datetime(2026, 10, 1, 9, 25, tzinfo=schedule_store.TZ))
        two = overview.anomaly_stats(2, now=self.now)
        assert two['start'] == '2026-11-07' and two['end'] == '2026-11-20'
        assert [item['name'] for item in two['late']] == ['张三']
        four = overview.anomaly_stats(4, now=self.now)
        assert four['start'] == '2026-10-24'
        assert [item['name'] for item in four['late']] == ['张三']

    def test_short_history_still_reports_what_exists(self):
        only = self.shift('2026-11-19', ['张三'])
        self.stamp(only, '张三', datetime(2026, 11, 19, 9, 25, tzinfo=schedule_store.TZ))
        data = overview.anomaly_stats(4, now=self.now)
        assert data['late'] == [{'name': '张三', 'count': 1}]

    def test_future_shifts_are_not_counted(self):
        # Starts at 19:00 while the clock reads 18:00, so it is still upcoming.
        future = self.shift('2026-11-20', ['张三'], start='19:00', end='20:00')
        data = overview.anomaly_stats(2, now=self.now)
        assert data['late'] == [] and data['absent'] == []

    def test_manual_override_is_respected(self):
        sid = self.shift('2026-11-18', ['张三'])
        self.stamp(sid, '张三', datetime(2026, 11, 18, 9, 5, tzinfo=schedule_store.TZ))
        assert overview.anomaly_stats(2, now=self.now)['late'] == []
        overview.set_member_status(sid, '张三', 'late')
        assert overview.anomaly_stats(2, now=self.now)['late'] == [{'name': '张三', 'count': 1}]

    def test_archived_check_ins_count(self):
        sid = self.shift('2026-11-17', ['张三'])
        self.stamp(sid, '张三', datetime(2026, 11, 17, 9, 25, tzinfo=schedule_store.TZ))
        schedule_store.clear_all()
        data = overview.anomaly_stats(2, now=self.now)
        assert data['late'] == [{'name': '张三', 'count': 1}]

    def test_empty_periods_return_empty_rankings(self):
        data = overview.anomaly_stats(2, now=self.now)
        assert data['late'] == [] and data['absent'] == []
        assert data['weeks'] == 2

    def test_pinyin_key_is_stable(self):
        assert overview.pinyin_key('张三') == ('zhangsan', '张三')
        assert overview.pinyin_key('阿') < overview.pinyin_key('包')
