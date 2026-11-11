from datetime import datetime
from unittest.mock import patch

import unittest
import test_schedule
from server import duty_settings, overview, schedule_store


class TestOverview(unittest.TestCase):
    setUp = test_schedule.ScheduleTest.setUp
    tearDown = test_schedule.ScheduleTest.tearDown

    def make(self, day, start, end, names):
        return schedule_store.save(dict(day=day, start=start, end=end, members=names))

    def stamp(self, sid, name, at):
        with patch.object(schedule_store, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = at
            schedule_store.check_in(sid, name)

    def test_distant_month_and_week_skip_intervening_shifts(self):
        self.make('2026-11-02', '09:00', '12:00', ['周内'])
        self.make('2026-12-01', '09:00', '12:00', ['间隔'])
        self.make('2027-02-01', '09:00', '12:00', ['月内'])
        with patch.object(overview, '_shift_payload', wraps=overview._shift_payload) as payload:
            data = overview.overview('2027-02', now=self.now, week='2026-11-02')
        self.assertNotIn('2026-12-01', [call.args[1]['day'] for call in payload.call_args_list])
        self.assertEqual(data['stats']['total'], 0)
        self.assertEqual(data['week']['shifts'], 1)
        self.assertEqual(data['week']['days'][0]['shifts'][0]['members'][0]['name'], '周内')

    def test_generated_week_keeps_original_slot_when_first_shift_is_empty(self):
        from server import weekly_store
        roster = {'needs_review': False, 'slots': [
            {'start': start, 'end': end, 'days': [(['甲'] if i == 0 and day == 0
                else ['乙'] if i > 0 and day == 3 else []) for day in range(7)]}
            for i, (start, end) in enumerate([
                ('08:30', '10:15'), ('10:15', '12:00'),
                ('13:30', '15:15'), ('15:15', '17:00')])]}
        weekly_store.save(roster)
        weekly_store.publish('2026-11-09')
        data = overview.overview('2026-11', now=self.now, week='2026-11-09')
        monday = data['week']['days'][0]['shifts']
        thursday = data['week']['days'][3]['shifts']
        self.assertEqual([s['weekly_slot'] for s in monday], [0])
        self.assertEqual([s['weekly_slot'] for s in thursday], [1, 2, 3])
        self.assertEqual([s['start'] for s in thursday], ['10:15', '13:30', '15:15'])

    def test_normal_late_absent_and_rate(self):
        now = datetime(2026, 11, 3, 12, 30, tzinfo=schedule_store.TZ)
        on_time = self.make('2026-11-02', '08:30', '10:00', ['甲'])
        late = self.make('2026-11-02', '10:15', '12:00', ['乙', '丙'])
        missed = self.make('2026-11-03', '08:30', '10:00', ['丁'])
        self.stamp(on_time, '甲', datetime(2026, 11, 2, 8, 25, tzinfo=schedule_store.TZ))
        self.stamp(late, '乙', datetime(2026, 11, 2, 10, 40, tzinfo=schedule_store.TZ))
        self.stamp(late, '丙', datetime(2026, 11, 2, 10, 20, tzinfo=schedule_store.TZ))
        data = overview.overview('2026-11', now=now)
        assert data['stats'] == {'normal': 2, 'total': 4, 'late': 1, 'absent': 1, 'rate': 50.0}
        week_shifts = [s for d in data['week']['days'] for s in d['shifts']]
        past = [s for s in week_shifts if s['day'] == '2026-11-02' and s['start'] == '10:15'][0]
        assert past['late'] == ['乙'] and past['absent'] == []
        missed_day = [s for s in week_shifts if s['day'] == '2026-11-03'][0]
        assert missed_day['absent'] == ['丁']
        assert missed_day['phase'] == 'past'

    def test_future_shift_is_pending_and_not_counted(self):
        now = datetime(2026, 11, 3, 8, 0, tzinfo=schedule_store.TZ)
        self.make('2026-11-05', '08:30', '10:00', ['甲'])
        data = overview.overview('2026-11', now=now)
        assert data['stats']['total'] == 0 and data['stats']['rate'] is None
        column = data['week']['days'][4]
        assert column['day'] == '2026-11-06' or column['day'] == '2026-11-05'
        future = [s for d in data['week']['days'] for s in d['shifts']][0]
        assert future['phase'] == 'future'
        assert [m['status'] for m in future['members']] == ['pending']

    def test_week_board_covers_monday_to_sunday_across_months(self):
        now = datetime(2026, 11, 3, 9, 0, tzinfo=schedule_store.TZ)
        self.make('2026-11-01', '08:30', '10:00', ['甲'])
        data = overview.overview('2026-12', now=now)
        assert data['week']['start'] == '2026-11-02'
        assert data['week']['end'] == '2026-11-08'
        assert [d['day'] for d in data['week']['days']][0] == '2026-11-02'
        assert data['month'] == '2026-12'

    def test_month_boundaries_include_last_day(self):
        now = datetime(2026, 11, 30, 23, 0, tzinfo=schedule_store.TZ)
        self.make('2026-11-30', '08:30', '10:00', ['甲'])
        self.make('2026-12-01', '08:30', '10:00', ['乙'])
        data = overview.overview('2026-11', now=now)
        assert [s['day'] for s in data['timeline'] if s['day'].startswith('2026-11-3')] == ['2026-11-30']
        assert data['stats']['total'] == 1

    def test_check_in_after_absence_threshold_is_absent_not_late(self):
        # Shift starts 09:00; late >15 minutes, absent >30 minutes, both from 09:00.
        duty_settings.save({'early_minutes': 10, 'late_minutes': 15, 'absent_minutes': 30})
        sid = self.make('2026-11-02', '09:00', '11:00', ['早到', '迟到', '旷到'])
        self.stamp(sid, '早到', datetime(2026, 11, 2, 9, 10, tzinfo=schedule_store.TZ))
        self.stamp(sid, '迟到', datetime(2026, 11, 2, 9, 20, tzinfo=schedule_store.TZ))
        self.stamp(sid, '旷到', datetime(2026, 11, 2, 9, 40, tzinfo=schedule_store.TZ))
        data = overview.overview('2026-11', now=datetime(2026, 11, 2, 12, 0, tzinfo=schedule_store.TZ))
        shift = [s for d in data['week']['days'] for s in d['shifts'] if s['start'] == '09:00'][0]
        statuses = {m['name']: m['status'] for m in shift['members']}
        assert statuses == {'早到': 'normal', '迟到': 'late', '旷到': 'absent'}
        assert shift['late'] == ['迟到']
        assert shift['absent'] == ['旷到']
        assert data['stats'] == {'normal': 1, 'total': 3, 'late': 1, 'absent': 1, 'rate': 33.3}

    def test_absence_window_uses_shift_start_even_for_overnight(self):
        duty_settings.save({'early_minutes': 10, 'late_minutes': 10, 'absent_minutes': 20})
        sid = self.make('2026-11-03', '23:00', '01:00', ['夜班'])
        self.stamp(sid, '夜班', datetime(2026, 11, 3, 23, 35, tzinfo=schedule_store.TZ))
        data = overview.overview('2026-11', now=datetime(2026, 11, 4, 0, 10, tzinfo=schedule_store.TZ))
        shift = [s for s in data['timeline'] if s['id'] == sid][0]
        assert shift['members'][0]['status'] == 'absent'

    def test_rules_change_shift_classification(self):
        now = datetime(2026, 11, 2, 12, 0, tzinfo=schedule_store.TZ)
        sid = self.make('2026-11-02', '09:00', '10:00', ['甲'])
        self.stamp(sid, '甲', datetime(2026, 11, 2, 9, 20, tzinfo=schedule_store.TZ))
        duty_settings.save({'late_minutes': 30, 'absent_minutes': 60})
        data = overview.overview('2026-11', now=now)
        assert data['stats']['late'] == 0 and data['stats']['normal'] == 1

    def test_manual_status_override_wins(self):
        now = datetime(2026, 11, 2, 12, 0, tzinfo=schedule_store.TZ)
        sid = self.make('2026-11-02', '09:00', '10:00', ['甲'])
        self.stamp(sid, '甲', datetime(2026, 11, 2, 9, 5, tzinfo=schedule_store.TZ))
        data = overview.overview('2026-11', now=now)
        shift = [s for s in data['timeline'] if s['id'] == sid][0]
        assert shift['members'][0]['status'] == 'normal'
        overview.set_member_status(sid, '甲', 'late')
        data = overview.overview('2026-11', now=now)
        shift = [s for s in data['timeline'] if s['id'] == sid][0]
        assert shift['members'][0]['status'] == 'late'
        assert data['stats']['late'] == 1

    def test_manual_status_rejects_unknown_values(self):
        sid = self.make('2026-11-02', '09:00', '10:00', ['甲'])
        with self.assertRaises(ValueError):
            overview.set_member_status(sid, '甲', 'pending')
        with self.assertRaises(LookupError):
            overview.set_member_status(sid, '不存在', 'late')


class TestOverviewWeekNavigation(unittest.TestCase):
    setUp = test_schedule.ScheduleTest.setUp
    tearDown = test_schedule.ScheduleTest.tearDown

    def make(self, day, start='09:00', end='10:00', names=('甲',)):
        return schedule_store.save(dict(day=day, start=start, end=end, members=list(names)))

    def test_current_week_is_the_anchor_by_default(self):
        self.make('2026-11-04')
        now = datetime(2026, 11, 5, 12, 0, tzinfo=schedule_store.TZ)
        week = overview.overview('2026-11', now=now)['week']
        assert week['start'] == '2026-11-02' and week['end'] == '2026-11-08'
        assert week['current'] == '2026-11-02'

    def test_previous_and_next_follow_the_requested_week(self):
        self.make('2026-10-21')
        self.make('2026-11-04')
        self.make('2026-11-18')
        now = datetime(2026, 11, 5, 12, 0, tzinfo=schedule_store.TZ)
        back = overview.overview('2026-11', now=now, week='2026-10-21')['week']
        assert back['start'] == '2026-10-19'
        assert back['previous'] is None                    # earliest recorded week
        assert back['next'] == '2026-10-26'
        assert [s['day'] for d in back['days'] for s in d['shifts']] == ['2026-10-21']
        # Gap weeks are still reachable, up to the newest recorded week.
        middle = overview.overview('2026-11', now=now, week='2026-10-28')['week']
        assert middle['previous'] == '2026-10-19' and middle['next'] == '2026-11-02'

    def test_any_day_in_the_week_anchors_the_same_board(self):
        self.make('2026-11-04')
        now = datetime(2026, 11, 5, 12, 0, tzinfo=schedule_store.TZ)
        for anchor in ('2026-11-02', '2026-11-04', '2026-11-08'):
            week = overview.overview('2026-11', now=now, week=anchor)['week']
            assert week['start'] == '2026-11-02', anchor
            # The only shift is this week's, so it is both bound and the current week.
            assert week['previous'] is None and week['next'] is None, anchor

    def test_arrows_disable_at_the_recorded_bounds(self):
        first = self.make('2026-11-04')
        last = self.make('2026-11-18')
        now = datetime(2026, 11, 5, 12, 0, tzinfo=schedule_store.TZ)
        earliest = overview.overview('2026-11', now=now, week='2026-11-04')['week']
        assert earliest['first'] == '2026-11-02'
        assert earliest['previous'] is None and earliest['next'] == '2026-11-09'
        latest = overview.overview('2026-11', now=now, week='2026-11-18')['week']
        assert latest['last'] == '2026-11-16'
        assert latest['next'] is None and latest['previous'] == '2026-11-09'
        assert first and last

    def test_bounds_always_include_the_current_week(self):
        # Every shift is in the past, yet this week stays reachable.
        self.make('2026-10-07')
        now = datetime(2026, 11, 5, 12, 0, tzinfo=schedule_store.TZ)
        week = overview.overview('2026-11', now=now)['week']
        assert week['last'] == '2026-11-02'
        assert week['next'] is None
        assert week['previous'] == '2026-10-26'

    def test_empty_schedule_disables_both_arrows(self):
        now = datetime(2026, 11, 5, 12, 0, tzinfo=schedule_store.TZ)
        week = overview.overview('2026-11', now=now)['week']
        assert week['previous'] is None and week['next'] is None
        assert week['first'] == week['last'] == '2026-11-02'

    def test_invalid_week_is_rejected(self):
        with self.assertRaises(ValueError):
            overview.overview('2026-11', now=datetime(2026, 11, 5, 12, 0, tzinfo=schedule_store.TZ),
                              week='not-a-day')


class TestWeekBulkOperations(unittest.TestCase):
    setUp = test_schedule.ScheduleTest.setUp
    tearDown = test_schedule.ScheduleTest.tearDown

    def make(self, day, start='09:00', end='10:00', names=('甲',)):
        return schedule_store.save(dict(day=day, start=start, end=end, members=list(names)))

    def test_week_summary_counts_shifts_people_and_check_ins(self):
        first = self.make('2026-11-02', names=['甲', '乙'])
        second = self.make('2026-11-04')
        self.make('2026-11-09')  # next week must not count
        with patch.object(schedule_store, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 2, 9, 2, tzinfo=schedule_store.TZ)
            schedule_store.check_in(first, '甲')
        summary = schedule_store.week_summary('2026-11-02')
        assert summary['week_start'] == '2026-11-02' and summary['week_end'] == '2026-11-08'
        assert summary['shifts'] == 2
        assert summary['people'] == 3
        assert summary['checked'] == 1
        assert second

    def test_clear_week_archives_check_ins_and_leaves_other_weeks(self):
        kept = self.make('2026-11-09')
        gone = self.make('2026-11-03', names=['张三'])
        with patch.object(schedule_store, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 3, 9, 2, tzinfo=schedule_store.TZ)
            schedule_store.check_in(gone, '张三')
        result = schedule_store.clear_week('2026-11-02')
        assert result == {'week_start': '2026-11-02', 'week_end': '2026-11-08',
                          'deleted': 1, 'archived': 1}
        days = {row['day'] for row in schedule_store.list_day('2026-11-09')}
        assert days == {'2026-11-09'}, kept
        assert schedule_store.week_summary('2026-11-02')['shifts'] == 0
        archived = schedule_store.history()
        assert [(row['day'], row['name']) for row in archived] == [('2026-11-03', '张三')]

    def test_clear_week_rejects_a_non_monday(self):
        with self.assertRaises(ValueError):
            schedule_store.clear_week('2026-11-04')

    def test_clear_week_tolerates_an_empty_week(self):
        result = schedule_store.clear_week('2026-11-02')
        assert result['deleted'] == 0 and result['archived'] == 0

    def test_overview_week_reports_its_totals(self):
        self.make('2026-11-02', names=['甲', '乙'])
        now = datetime(2026, 11, 2, 12, 0, tzinfo=schedule_store.TZ)
        week = overview.overview('2026-11', now=now)['week']
        assert week['shifts'] == 1 and week['people'] == 2 and week['checked'] == 0
