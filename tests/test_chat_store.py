import unittest
from unittest.mock import patch

import test_schedule
from server import chat_store, db


class ChatStoreTest(unittest.TestCase):
    setUp = test_schedule.ScheduleTest.setUp
    tearDown = test_schedule.ScheduleTest.tearDown

    def save(self, visitor='visitor', count=3):
        sessions = [dict(id=str(i), title='会话', updatedAt=i,
                         messages=[{'role': 'user', 'content': f'你好{i}'},
                                   {'role': 'assistant', 'content': '答复'}])
                    for i in range(count)]
        chat_store.save_visitor_chats(visitor, sessions, str(count - 1))
        return sessions

    def test_single_session_reads_only_requested_messages(self):
        self.save()
        self.save('other')
        expected = chat_store.load_visitor_chats('visitor')['sessions'][1]
        with patch.object(chat_store, '_session_from_row', wraps=chat_store._session_from_row) as read:
            self.assertEqual(chat_store.get_session(' visitor ', '1'), expected)
            read.assert_called_once()
        self.assertIsNone(chat_store.get_session('missing', '1'))
        self.assertIsNone(chat_store.get_session('visitor', 'missing'))

    def test_batch_save_preserves_order_images_and_replacement(self):
        sessions = self.save()
        sessions[0]['messages'].append({'role': 'user', 'content': [
            {'type': 'text', 'text': '图片'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,YQ=='}}]})
        chat_store.save_visitor_chats('visitor', sessions[:1], '0')
        packed = chat_store.load_visitor_chats('visitor')
        self.assertEqual(packed['currentId'], '0')
        self.assertEqual(len(packed['sessions']), 1)
        self.assertEqual(packed['sessions'][0]['messages'], sessions[0]['messages'])

    def test_eviction_measures_global_usage_once_and_removes_oldest(self):
        self.save(count=5)
        with db.write() as conn:
            conn.execute('UPDATE messages SET content=?', ('x' * 140000,))
            with patch.object(chat_store, '_usage_bytes', wraps=chat_store._usage_bytes) as usage:
                chat_store._evict_oldest(conn, 1)
                usage.assert_called_once_with(conn)
            self.assertLessEqual(chat_store._usage_bytes(conn), 1024 * 1024)
        self.assertEqual([s['id'] for s in chat_store.load_visitor_chats('visitor')['sessions']],
                         ['4', '3', '2'])

    def test_eviction_removes_empty_visitors_and_handles_empty_database(self):
        self.save(count=1)
        with db.write() as conn:
            conn.execute('UPDATE messages SET content=?', ('x' * 600000,))
            chat_store._evict_oldest(conn, 1)
            self.assertEqual(chat_store._usage_bytes(conn), 0)
            self.assertIsNone(conn.execute('SELECT * FROM visitors').fetchone())
            chat_store._evict_oldest(conn, 1)
