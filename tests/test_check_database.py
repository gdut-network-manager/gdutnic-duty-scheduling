import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import MagicMock, patch

from server import check_database, db


class CheckDatabaseTest(unittest.TestCase):
    def check_failure(self, code, host='127.0.0.1'):
        error = RuntimeError('MySQL 连接失败')
        error.__cause__ = Exception(code, 'message containing secret-password')
        output = io.StringIO()
        with patch.dict(os.environ, {'DB_BACKEND': 'mysql', 'MYSQL_HOST': host, 'MYSQL_PORT': '3307'}), \
                patch.object(db, 'read', side_effect=error), \
                patch.object(db, '_conn', None), redirect_stderr(output):
            self.assertEqual(check_database.main(), 1)
        self.assertNotIn('secret-password', output.getvalue())
        self.assertIn(f'{host}:3307', output.getvalue())
        return output.getvalue()

    def test_refused_local_connection_has_recovery_command(self):
        output = self.check_failure(2003)
        self.assertIn('先启动 Docker', output)
        self.assertIn('docker compose --env-file data/.env up -d --wait mysql', output)

    def test_remote_connection_does_not_suggest_local_database(self):
        output = self.check_failure(2003, 'db.example.com')
        self.assertNotIn('docker compose', output)

    def test_direct_connection_error_also_explains_local_recovery(self):
        import pymysql
        with patch.dict(os.environ, {'DB_BACKEND': 'mysql', 'MYSQL_HOST': '127.0.0.1', 'MYSQL_PORT': '3307'}), \
                patch.object(db, '_conn', None), \
                patch('pymysql.connect', side_effect=pymysql.OperationalError(2003, 'connection refused')):
            with self.assertRaises(RuntimeError) as caught:
                db.connect()
        self.assertIn('127.0.0.1:3307', str(caught.exception))
        self.assertIn('docker compose --env-file data/.env up -d --wait mysql', str(caught.exception))

    def test_authentication_and_missing_database_errors(self):
        self.assertIn('拒绝认证', self.check_failure(1045))
        self.assertIn('数据库不存在', self.check_failure(1049))
        self.assertIn('TLS', self.check_failure(2026))

    def test_success_checks_query_and_closes_connection(self):
        conn = MagicMock()
        with patch.dict(os.environ, {'DB_BACKEND': 'mysql'}), \
                patch.object(db, 'read') as read, patch.object(db, '_conn', conn), \
                redirect_stdout(io.StringIO()):
            read.return_value.__enter__.return_value = conn
            self.assertEqual(check_database.main(), 0)
            conn.execute.assert_called_once_with('SELECT 1')
            conn.close.assert_called_once()
            self.assertIsNone(db._conn)

    def test_sqlite_failure_has_no_mysql_instructions(self):
        output = io.StringIO()
        with patch.dict(os.environ, {'DB_BACKEND': 'sqlite'}), \
                patch.object(db, 'read', side_effect=OSError('private path')), \
                patch.object(db, '_conn', None), redirect_stderr(output):
            self.assertEqual(check_database.main(), 1)
        self.assertIn('文件权限', output.getvalue())
        self.assertNotIn('MYSQL', output.getvalue())
        self.assertNotIn('private path', output.getvalue())
