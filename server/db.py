from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from server.init_data import initialize

ROOT = Path(__file__).resolve().parent.parent
DATA = initialize(ROOT)
# Both start scripts and CLI maintenance commands use the same configuration.
try:
    from dotenv import load_dotenv
    load_dotenv(DATA / ".env")
except ImportError:
    if (DATA / ".env").exists():
        raise RuntimeError("请先安装 requirements.txt 中的依赖")
DB_PATH = DATA / "app.sqlite"

_lock = threading.RLock()
_conn: Any = None
_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS push_deliveries (
 id TEXT PRIMARY KEY, period_end TEXT NOT NULL, recipient TEXT NOT NULL,
 state TEXT NOT NULL, updated_at TEXT NOT NULL, error TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
  username TEXT PRIMARY KEY,
  password TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS member_accounts (
 username TEXT PRIMARY KEY, password TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
 permissions TEXT NOT NULL DEFAULT '', group_id TEXT NOT NULL DEFAULT '', sso_id TEXT NOT NULL DEFAULT '', sso_auto_link_blocked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS login_sessions (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, username TEXT NOT NULL,
 last_activity REAL NOT NULL, expires_at REAL NOT NULL, sso_identity TEXT NOT NULL DEFAULT '', last_response REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS keycloak_links (
 identity_key TEXT PRIMARY KEY, issuer TEXT NOT NULL, subject TEXT NOT NULL,
 username TEXT NOT NULL UNIQUE, account_key TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS keycloak_flows (
 id TEXT PRIMARY KEY, expires_at INTEGER NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS permission_groups (
 id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, permissions TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS shifts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, start TEXT NOT NULL,
 end TEXT NOT NULL, location TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
 special INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_shifts_day ON shifts(day,start);
CREATE TABLE IF NOT EXISTS attendance (
 shift_id INTEGER NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
 name TEXT NOT NULL, checked_at TEXT, status_override TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(shift_id,name)
);
CREATE TABLE IF NOT EXISTS swaps (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 shift_a INTEGER NOT NULL, name_a TEXT NOT NULL,
 shift_b INTEGER NOT NULL, name_b TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE (shift_a, name_a), UNIQUE (shift_b, name_b)
);
CREATE TABLE IF NOT EXISTS operation_log (
 id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL,
 role TEXT NOT NULL DEFAULT '', action TEXT NOT NULL, detail TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_operation_log_at ON operation_log(at);
CREATE TABLE IF NOT EXISTS attendance_history (
 id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL, start TEXT NOT NULL,
 end TEXT NOT NULL, name TEXT NOT NULL, checked_at TEXT NOT NULL,
 location TEXT NOT NULL DEFAULT '', note TEXT NOT NULL, archived_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS weekly_instances (
 day TEXT NOT NULL, slot INTEGER NOT NULL,
 shift_id INTEGER NOT NULL UNIQUE REFERENCES shifts(id) ON DELETE CASCADE,
 PRIMARY KEY(day,slot)
);
"""
# Added after the initial release; existing databases need an in-place upgrade.
COLUMNS = {'shifts': {'special': 'INTEGER NOT NULL DEFAULT 0'},
           'member_accounts': {'permissions': "VARCHAR(191) NOT NULL DEFAULT ''",
                               'group_id': "VARCHAR(64) NOT NULL DEFAULT ''",
                               'sso_id': "VARCHAR(64) NOT NULL DEFAULT ''",
                               'sso_auto_link_blocked': 'TINYINT NOT NULL DEFAULT 0'},
           'keycloak_links': {'linked_at': "VARCHAR(40) NOT NULL DEFAULT ''"},
           'login_sessions': {'sso_identity': "VARCHAR(64) NOT NULL DEFAULT ''", 'last_response': 'DOUBLE NOT NULL DEFAULT 0'},
           'attendance': {'status_override': "VARCHAR(16) NOT NULL DEFAULT ''", 'final_status': "VARCHAR(16) NOT NULL DEFAULT ''"},
           'attendance_history': {'final_status': "VARCHAR(16) NOT NULL DEFAULT ''"},
           'field_duty': {'manual': 'TINYINT NOT NULL DEFAULT 0', 'status': 'TINYINT NOT NULL DEFAULT 2'}}



def backend() -> str:
    value = os.environ.get("DB_BACKEND", "mysql").lower()
    if value not in {"mysql", "sqlite"}:
        raise RuntimeError("DB_BACKEND 仅支持 mysql 或 sqlite")
    return value


class MysqlResult:
    def __init__(self, cursor):
        # PyMySQL does not execute empty batches and leaves lastrowid unset.
        self.rowcount = max(0, cursor.rowcount)
        self.lastrowid = getattr(cursor, 'lastrowid', None)
        self.rows = list(cursor.fetchall()) if cursor.description else []
        self.offset = 0

    def fetchone(self):
        if self.offset >= len(self.rows):
            return None
        row = self.rows[self.offset]
        self.offset += 1
        return row

    def fetchall(self):
        rows = self.rows[self.offset:]
        self.offset = len(self.rows)
        return rows

    def __iter__(self):
        return iter(self.fetchall())


class MysqlConnection:
    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql, args=()):
        with self.raw.cursor() as cursor:
            cursor.execute(sql.replace("?", "%s"), args or None)
            return MysqlResult(cursor)

    def executemany(self, sql, args):
        with self.raw.cursor() as cursor:
            cursor.executemany(sql.replace("?", "%s"), args)
            return MysqlResult(cursor)

    def commit(self): self.raw.commit()
    def rollback(self): self.raw.rollback()
    def close(self): self.raw.close()


def connect():
    global _conn
    with _lock:
        if _conn is not None:
            if isinstance(_conn, MysqlConnection):
                import pymysql
                # Reconnect only before a transaction, never in the middle of a write.
                try:
                    _conn.raw.ping()
                except pymysql.Error:
                    if _conn.raw.open:
                        _conn.close()
                    _conn = None
            if _conn is not None:
                return _conn
        if backend() == "mysql":
            import pymysql
            options = dict(
                host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
                port=int(os.environ.get("MYSQL_PORT", "3306")),
                user=os.environ.get("MYSQL_USER", "gdutnic"),
                password=os.environ.get("MYSQL_PASSWORD", ""),
                database=os.environ.get("MYSQL_DATABASE", "gdutnic"),
                charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
                autocommit=True, connect_timeout=10, read_timeout=30, write_timeout=30,
            )
            if os.environ.get("MYSQL_SSL_CA"):
                certificate = Path(os.environ["MYSQL_SSL_CA"])
                if not certificate.is_absolute():
                    certificate = DATA / certificate
                options.update(ssl_ca=str(certificate), ssl_verify_cert=True, ssl_verify_identity=True)
            try:
                conn = MysqlConnection(pymysql.connect(**options))
            except pymysql.MySQLError as exc:
                message = (f"MySQL 连接失败（{options['host']}:{options['port']}），"
                           "请检查 data/.env 中的 MYSQL_* 配置和数据库服务。")
                if exc.args and exc.args[0] in (2002, 2003) and options['host'] in ('127.0.0.1', 'localhost', '::1'):
                    message += (" 使用项目自带 MySQL 时，请先启动 Docker Desktop / Docker 服务，"
                                "然后执行 docker compose --env-file data/.env up -d --wait mysql")
                raise RuntimeError(message) from exc
            try:
                schema = (ROOT / "sql" / "mysql_schema.sql").read_text(encoding="utf-8")
                for statement in schema.split(";"):
                    if statement.strip(): conn.execute(statement)
                for table, columns in COLUMNS.items():
                    for column, definition in columns.items():
                        if not conn.execute(
                            "SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()"
                            " AND TABLE_NAME=? AND COLUMN_NAME=?", (table, column)).fetchone():
                            conn.execute(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}")
                            if table == "login_sessions" and column == "sso_identity":
                                # Older leases do not identify their login method; require reauthentication.
                                conn.execute("DELETE FROM login_sessions")
            except Exception:
                conn.close()
                raise
            _conn = conn
        else:
            DATA.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(SCHEMA)
            for table, columns in COLUMNS.items():
                present = {row["name"] for row in conn.execute(f"PRAGMA table_info(`{table}`)")}
                if not present:
                    # Some tables (field_duty) are created lazily by their own module.
                    continue
                for column, definition in columns.items():
                    if column not in present:
                        # VARCHAR is valid in SQLite, but TEXT matches the declared schema.
                        if isinstance(definition, str):
                            definition = definition.replace('VARCHAR(191)', 'TEXT')
                        conn.execute(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}")
                        if table == "login_sessions" and column == "sso_identity":
                            # Older leases do not identify their login method; require reauthentication.
                            conn.execute("DELETE FROM login_sessions")
            _conn = conn
            _migrate_json(conn)
            _retire_legacy_json()
        return _conn


def upgrade_table(table):
    """Add any columns this release needs once a lazily created table exists."""
    columns = COLUMNS.get(table) or {}
    if not columns or _conn is None:
        return
    with _lock:
        if isinstance(_conn, MysqlConnection):
            present = {row["COLUMN_NAME"] for row in _conn.execute(
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE()"
                " AND TABLE_NAME=?", (table,)).fetchall()}
        else:
            present = {row["name"] for row in _conn.execute(f"PRAGMA table_info(`{table}`)")}
        for column, definition in columns.items():
            if column not in present:
                if not isinstance(_conn, MysqlConnection):
                    definition = definition.replace('VARCHAR(191)', 'TEXT').replace('TINYINT', 'INTEGER')
                _conn.execute(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}")
                if table == "login_sessions" and column == "sso_identity":
                    # Older leases do not identify their login method; require reauthentication.
                    _conn.execute("DELETE FROM login_sessions")


@contextmanager
def read():
    with _lock:
        # Nested reads during publication must use the same live transaction.
        yield _conn if getattr(_local, "writing", False) else connect()


@contextmanager
def write():
    with _lock:
        if getattr(_local, "writing", False):
            raise RuntimeError("不支持嵌套写事务")
        conn = connect()
        mysql = isinstance(conn, MysqlConnection)
        acquired = False
        lock_name = "gdutnic_write_" + __import__("hashlib").sha256(
            os.environ.get("MYSQL_DATABASE", "gdutnic").encode()).hexdigest()[:32]
        try:
            if mysql:
                # Serialize multi-process writes as well as same-process writes.
                acquired = conn.execute("SELECT GET_LOCK(?, 30) AS acquired", (lock_name,)).fetchone()["acquired"] == 1
                if not acquired: raise RuntimeError("数据库忙，请稍后重试")
            conn.execute("START TRANSACTION" if mysql else "BEGIN IMMEDIATE")
            _local.writing = True
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            _local.writing = False
            if acquired:
                conn.execute("SELECT RELEASE_LOCK(?)", (lock_name,))


def upsert(conn, table, values, keys):
    columns = list(values)
    quoted = ", ".join(f"`{c}`" for c in columns)
    sql = f"INSERT INTO `{table}` ({quoted}) VALUES ({','.join('?' for _ in columns)})"
    updates = [c for c in columns if c not in keys]
    if isinstance(conn, MysqlConnection):
        sql += " ON DUPLICATE KEY UPDATE " + ", ".join(f"`{c}`=VALUES(`{c}`)" for c in updates)
    else:
        sql += " ON CONFLICT(" + ",".join(f"`{c}`" for c in keys) + ") DO UPDATE SET "
        sql += ", ".join(f"`{c}`=excluded.`{c}`" for c in updates)
    return conn.execute(sql, tuple(values[c] for c in columns))


def insert_missing(conn, table, values, keys):
    columns = list(values)
    sql = f"INSERT INTO `{table}` ({','.join('`'+c+'`' for c in columns)}) VALUES ({','.join('?' for _ in columns)})"
    if isinstance(conn, MysqlConnection):
        # Unlike INSERT IGNORE, this never masks invalid/truncated data.
        sql += f" ON DUPLICATE KEY UPDATE `{keys[0]}`=`{keys[0]}`"
    else:
        sql += " ON CONFLICT(" + ",".join(f"`{c}`" for c in keys) + ") DO NOTHING"
    return conn.execute(sql, tuple(values[c] for c in columns))


def get_setting(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM settings WHERE `key` = ?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return default


def set_setting(conn, key: str, value: Any) -> None:
    upsert(conn, "settings", {"key": key, "value": json.dumps(value, ensure_ascii=False)}, ["key"])


def _count(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return int(row["n"] if row else 0)


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _retire(path: Path) -> None:
    if not path.exists():
        return
    bak = path.with_name(path.name + ".bak")
    try:
        path.replace(bak)
    except OSError as exc:
        print(f"[db] 无法归档 {path.name}: {exc}")


def _retire_legacy_json() -> None:
    _retire(DATA / "admin.json")


def _migrate_json(conn: sqlite3.Connection) -> None:
    admin = _read_json(DATA / "admin.json")
    changed = False
    conn.execute("BEGIN")
    try:
        if admin and isinstance(admin, dict) and _count(conn, "settings") == 0:
            _import_admin(conn, admin)
            changed = True
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    if changed:
        print(f"[db] 已迁移 JSON 到 SQLite: {DB_PATH}")


def _import_admin(conn: sqlite3.Connection, data: dict[str, Any]) -> None:
    set_setting(conn, "path", str(data.get("path") or ""))
    set_setting(conn, "username", str(data.get("username") or ""))
    set_setting(conn, "password", str(data.get("password") or ""))
    users = data.get("users")
    if not isinstance(users, list):
        users = []
    name = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    if name and password and not any(isinstance(u, dict) and u.get("username") == name for u in users):
        users.append({"username": name, "password": password})
    for item in users:
        if not isinstance(item, dict):
            continue
        username = str(item.get("username") or "").strip()
        hashed = str(item.get("password") or "")
        if username and hashed:
            conn.execute(
                "INSERT OR REPLACE INTO users(username, password) VALUES(?, ?)",
                (username, hashed),
            )
