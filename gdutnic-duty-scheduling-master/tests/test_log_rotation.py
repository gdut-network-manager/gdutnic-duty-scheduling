"""Log rotation: the audit trail and the log files are capped at 10 MB, trimmed to 5 MB."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server import db, log_rotation, operation_log


class LogRotationTest(unittest.TestCase):
    """A throw-away SQLite database and a throw-away log directory per test."""

    def setUp(self):
        self.env = patch.dict(os.environ, {'DB_BACKEND': 'sqlite'})
        self.env.start()
        self.temp = tempfile.TemporaryDirectory()
        self.old = (db.DATA, db.DB_PATH, db._conn)
        db.DATA = Path(self.temp.name)
        db.DB_PATH = db.DATA / 'test.sqlite'
        db._conn = None
        self.logs = db.DATA / 'logs'
        self.logs.mkdir()

    def tearDown(self):
        if db._conn:
            db._conn.close()
        db.DATA, db.DB_PATH, db._conn = self.old
        self.temp.cleanup()
        self.env.stop()

    def test_policy_is_ten_megabytes_then_five(self):
        for module in (operation_log, log_rotation):
            self.assertEqual(module.LIMIT_BYTES, 10 * 1024 * 1024)
            self.assertEqual(module.KEEP_BYTES, 5 * 1024 * 1024)

    def test_operation_log_trims_oldest_rows_down_to_the_keep_threshold(self):
        with self.settings(operation_log, LIMIT_BYTES=20_000, KEEP_BYTES=10_000, CHECK_EVERY=10):
            for index in range(400):
                operation_log.record(f'actor{index}', '动作' * 20, '细节' * 100)
            # The periodic check can leave a few rows written after the last prune.
            self.assertLessEqual(operation_log.stats()['used_bytes'], 30_000)
            operation_log.prune()
            stats = operation_log.stats()
        self.assertLessEqual(stats['used_bytes'], 10_000)
        self.assertLess(stats['rows'], 400)
        # The newest rows survive: the page still shows recent activity.
        newest = operation_log.list_entries(5)
        self.assertTrue(newest)
        self.assertEqual(newest[0]['actor'], 'actor399')

    def test_operation_log_does_not_trim_below_the_limit(self):
        with self.settings(operation_log, LIMIT_BYTES=10 * 1024 * 1024, KEEP_BYTES=5 * 1024 * 1024):
            for index in range(30):
                operation_log.record(f'actor{index}', '登录', '少量内容')
            stats = operation_log.stats()
        self.assertEqual(stats['rows'], 30)

    def test_prune_is_a_no_op_under_the_limit(self):
        with self.settings(operation_log, LIMIT_BYTES=10 * 1024 * 1024):
            operation_log.record('alice', '登录管理端')
            self.assertEqual(operation_log.prune(), 0)
            self.assertEqual(operation_log.stats()['rows'], 1)

    def test_log_files_are_trimmed_to_the_keep_threshold(self):
        directory = self.logs
        log_file = directory / 'server.log'
        line = b'INFO: 127.0.0.1 - "GET / HTTP/1.1" 200 OK\n'
        log_file.write_bytes(line * 1000)  # about 46 KB
        with self.settings(log_rotation, LIMIT_BYTES=20_000, KEEP_BYTES=10_000):
            self.assertGreater(log_file.stat().st_size, 20_000)
            freed = log_rotation.prune()
            self.assertGreater(freed, 0)
            self.assertLessEqual(log_file.stat().st_size, 10_000)
            content = log_file.read_bytes()
        # Only complete lines remain, and the newest lines are the ones kept.
        self.assertTrue(content.endswith(line))
        self.assertTrue(content.startswith(b'INFO:'))
        self.assertEqual(content.count(b'\n'), len(content.split(b'\n')) - 1)

    def test_log_files_under_the_limit_are_untouched(self):
        log_file = self.logs / 'quiet.log'
        log_file.write_bytes(b'short log\n')
        self.assertEqual(log_rotation.prune(), 0)
        self.assertEqual(log_file.read_bytes(), b'short log\n')

    def test_missing_log_directory_is_not_an_error(self):
        self.logs.rmdir()
        self.assertEqual(log_rotation.files(), [])
        self.assertEqual(log_rotation.size(), 0)
        self.assertEqual(log_rotation.prune(), 0)

    def settings(self, module, **values):
        return _Patch(module, values)


class _Patch:
    """Temporarily override module-level constants for one test."""

    def __init__(self, module, values):
        self.module = module
        self.values = values
        self.saved = {}

    def __enter__(self):
        for name, value in self.values.items():
            self.saved[name] = getattr(self.module, name)
            setattr(self.module, name, value)
        return self

    def __exit__(self, *exc):
        for name, value in self.saved.items():
            setattr(self.module, name, value)
        return False
