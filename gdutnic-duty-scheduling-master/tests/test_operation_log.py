import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server import db, operation_log


class TestOperationLog(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict('os.environ', {'DB_BACKEND': 'sqlite'})
        self.env.start()
        self.temp = tempfile.TemporaryDirectory()
        self.old = (db.DATA, db.DB_PATH, db._conn)
        db.DATA = Path(self.temp.name)
        db.DB_PATH = db.DATA / 'test.sqlite'
        db._conn = None

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db.DATA, db.DB_PATH, db._conn = self.old
        self.temp.cleanup()
        self.env.stop()

    def test_records_time_actor_and_content(self):
        operation_log.record('root', '新增班次', '2026-11-06 08:00–10:00', role='admin')
        [entry] = operation_log.list_entries()
        assert entry['actor'] == 'root'
        assert entry['action'] == '新增班次'
        assert '08:00' in entry['detail']
        assert entry['role'] == 'admin'
        assert entry['at']

    def test_newest_first(self):
        operation_log.record('a', '第一步')
        operation_log.record('b', '第二步')
        actions = [entry['action'] for entry in operation_log.list_entries()]
        assert actions == ['第二步', '第一步']

    def test_filters_by_actor_and_keyword(self):
        operation_log.record('root', '新增班次', '2026-11-06')
        operation_log.record('ops', '删除账号', '张三')
        operation_log.record('root', '更改权限', '张三 → user')
        assert [e['actor'] for e in operation_log.list_entries(actor='root')] == ['root', 'root']
        assert [e['action'] for e in operation_log.list_entries(action='张三')] == ['更改权限', '删除账号']
        assert operation_log.actors() == ['ops', 'root']

    def test_limit_is_bounded(self):
        for index in range(5):
            operation_log.record('root', f'动作{index}')
        assert len(operation_log.list_entries(limit=2)) == 2
        assert len(operation_log.list_entries(limit=1000)) == 5
        assert len(operation_log.list_entries(limit=0)) == 1

    def test_logging_never_breaks_the_caller(self):
        with patch.object(db, 'write', side_effect=RuntimeError('db down')):
            operation_log.record('root', '不应抛出')

    def test_blank_actor_is_stored_as_anonymous(self):
        operation_log.record('', '未知操作')
        assert operation_log.list_entries()[0]['actor'] == '匿名'
