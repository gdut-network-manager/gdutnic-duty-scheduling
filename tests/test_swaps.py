from datetime import datetime, timedelta
from unittest.mock import patch

import test_schedule
from server import schedule_store, swap_store


class TestSwaps(test_schedule.ScheduleTest):
    """Duty swaps between two people on two future shifts."""

    def setUp(self):
        super().setUp()
        self.future = datetime(2026, 11, 2, 8, 0, tzinfo=schedule_store.TZ)

    def make(self, day, start, end, names):
        return schedule_store.save(dict(day=day, start=start, end=end, members=names))

    def members(self, shift_id):
        with __import__('server.db', fromlist=['read']).read() as conn:
            return sorted(r['name'] for r in conn.execute(
                'SELECT name FROM attendance WHERE shift_id=?', (shift_id,)))

    def test_swap_moves_both_people_and_lists_record(self):
        a = self.make('2026-11-10', '09:00', '11:00', ['张三', '王五'])
        b = self.make('2026-11-11', '14:00', '16:00', ['李四', '赵六'])
        with patch.object(swap_store, '_now', return_value=self.future):
            swap_id = swap_store.create(a, '张三', b, '李四', confirmed=True)
            items = swap_store.list_swaps()
        assert [item['id'] for item in items] == [swap_id]
        assert items[0]['deletable'] is True
        assert items[0]['a']['person'] == '张三' and items[0]['a']['shift']['day'] == '2026-11-10'
        assert items[0]['b']['person'] == '李四' and items[0]['b']['shift']['day'] == '2026-11-11'
        assert self.members(a) == ['李四', '王五']
        assert self.members(b) == ['张三', '赵六']

    def test_confirmation_required(self):
        a = self.make('2026-11-10', '09:00', '11:00', ['张三'])
        b = self.make('2026-11-11', '14:00', '16:00', ['李四'])
        with patch.object(swap_store, '_now', return_value=self.future):
            try:
                swap_store.create(a, '张三', b, '李四', confirmed=False)
            except ValueError as exc:
                assert '协商' in str(exc)
            else:
                raise AssertionError('confirmation should be required')
            assert swap_store.list_swaps() == []

    def test_rejects_same_person_missing_member_and_started_shift(self):
        a = self.make('2026-11-10', '09:00', '11:00', ['张三'])
        b = self.make('2026-11-11', '14:00', '16:00', ['李四'])
        past = self.make('2026-10-01', '09:00', '11:00', ['王五'])
        with patch.object(swap_store, '_now', return_value=self.future):
            for args in ((a, '张三', a, '李四'), (a, '张三', b, '王五'), (past, '王五', b, '李四')):
                try:
                    swap_store.create(*args, confirmed=True)
                except (ValueError, LookupError):
                    continue
                raise AssertionError(f'should reject {args}')
            assert swap_store.list_swaps() == []

    def test_duplicate_swap_is_refused(self):
        a = self.make('2026-11-10', '09:00', '11:00', ['张三'])
        b = self.make('2026-11-11', '14:00', '16:00', ['李四'])
        with patch.object(swap_store, '_now', return_value=self.future):
            swap_store.create(a, '张三', b, '李四', confirmed=True)
            try:
                swap_store.create(a, '张三', b, '李四', confirmed=True)
            except ValueError:
                pass
            else:
                raise AssertionError('duplicate swap should be refused')

    def test_delete_restores_original_assignment(self):
        a = self.make('2026-11-10', '09:00', '11:00', ['张三', '王五'])
        b = self.make('2026-11-11', '14:00', '16:00', ['李四'])
        with patch.object(swap_store, '_now', return_value=self.future):
            swap_id = swap_store.create(a, '张三', b, '李四', confirmed=True)
            swap_store.delete(swap_id)
        assert self.members(a) == ['张三', '王五']
        assert self.members(b) == ['李四']
        assert swap_store.list_swaps() == []

    def test_delete_is_blocked_once_either_shift_starts(self):
        a = self.make('2026-11-10', '09:00', '11:00', ['张三'])
        b = self.make('2026-11-11', '14:00', '16:00', ['李四'])
        with patch.object(swap_store, '_now', return_value=self.future):
            swap_id = swap_store.create(a, '张三', b, '李四', confirmed=True)
        started = datetime(2026, 11, 10, 9, 5, tzinfo=schedule_store.TZ)
        with patch.object(swap_store, '_now', return_value=started):
            items = swap_store.list_swaps()
            assert items[0]['deletable'] is False
            try:
                swap_store.delete(swap_id)
            except ValueError as exc:
                assert '开始' in str(exc)
            else:
                raise AssertionError('delete should be blocked')

    def test_record_disappears_after_both_checks_in(self):
        a = self.make('2026-11-10', '09:00', '11:00', ['张三'])
        b = self.make('2026-11-11', '14:00', '16:00', ['李四'])
        with patch.object(swap_store, '_now', return_value=self.future):
            swap_store.create(a, '张三', b, '李四', confirmed=True)
        # After the swap, 李四 works shift a and 张三 works shift b.
        with patch.object(schedule_store, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 10, 9, 5, tzinfo=schedule_store.TZ)
            schedule_store.check_in(a, '李四')
            after_one = swap_store.list_swaps()
        assert len(after_one) == 1, 'one check-in is not enough'
        with patch.object(schedule_store, 'datetime', wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 11, 11, 14, 5, tzinfo=schedule_store.TZ)
            schedule_store.check_in(b, '张三')
        # The record is evaluated when the list is read, after both shifts have finished.
        with patch.object(swap_store, '_now', return_value=datetime(2026, 11, 11, 18, 0, tzinfo=schedule_store.TZ)):
            after_both = swap_store.list_swaps()
        assert after_both == [], 'record should disappear once both are done'

    def test_candidates_only_future_unswapped_shifts(self):
        first = self.make('2026-11-12', '09:00', '11:00', ['张三'])
        second = self.make('2026-11-13', '14:00', '16:00', ['张三'])
        other = self.make('2026-11-14', '09:00', '11:00', ['李四'])
        past = self.make('2026-10-01', '09:00', '11:00', ['张三'])
        with patch.object(swap_store, '_now', return_value=self.future):
            days = [item['day'] for item in swap_store.candidates('张三', None)]
            assert days == ['2026-11-12', '2026-11-13']
            assert past not in [item['id'] for item in swap_store.candidates('张三', None)]
            swap_store.create(first, '张三', other, '李四', confirmed=True)
            remaining = [item['day'] for item in swap_store.candidates('张三', None)]
            assert remaining == ['2026-11-13'], remaining

    def test_member_view_follows_the_swap(self):
        a = self.make('2026-11-10', '09:00', '11:00', ['张三', '王五'])
        b = self.make('2026-11-11', '14:00', '16:00', ['李四'])
        with patch.object(swap_store, '_now', return_value=self.future):
            swap_store.create(a, '张三', b, '李四', confirmed=True)
        assert [s['id'] for s in schedule_store.list_day('2026-11-10', '李四')] == [a]
        assert schedule_store.list_day('2026-11-10', '张三') == []
        assert [s['id'] for s in schedule_store.list_day('2026-11-11', '张三')] == [b]

    def test_swaps_work_across_weeks_and_months(self):
        # A shift this week swapped with one several weeks later.
        soon = self.make('2026-11-07', '09:00', '11:00', ['张三'])
        later = self.make('2026-12-05', '14:00', '16:00', ['李四'])
        with patch.object(swap_store, '_now', return_value=self.future):
            swap_store.create(soon, '张三', later, '李四', confirmed=True)
            items = swap_store.list_swaps()
        assert len(items) == 1
        assert items[0]['a']['shift']['day'] == '2026-11-07'
        assert items[0]['b']['shift']['day'] == '2026-12-05'
        assert self.members(soon) == ['李四']
        assert self.members(later) == ['张三']

    def test_candidates_span_every_future_week(self):
        for day in ('2026-11-07', '2026-11-14', '2026-11-28', '2026-12-19'):
            self.make(day, '09:00', '11:00', ['张三'])
        with patch.object(swap_store, '_now', return_value=self.future):
            days = [item['day'] for item in swap_store.candidates('张三', None)]
        assert days == ['2026-11-07', '2026-11-14', '2026-11-28', '2026-12-19'], days
