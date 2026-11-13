"""Copy a SQLite database into an empty MySQL database without changing the source."""
import argparse
import json
from contextlib import closing
import sqlite3
from datetime import datetime
from pathlib import Path

from server import db

TABLES = ('settings', 'users', 'permission_groups', 'member_accounts', 'shifts', 'attendance',
          'attendance_history', 'operation_log', 'weekly_instances', 'push_deliveries')


def migrate(source):
    if db.backend() != 'mysql':
        raise RuntimeError('迁移目标必须为 MySQL，请设置 DB_BACKEND=mysql')
    source = Path(source).resolve()
    if not source.is_file():
        raise ValueError(f'未找到 SQLite 源文件：{source}')
    reader = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
    reader.row_factory = sqlite3.Row
    backup_dir = db.DATA / 'backups'
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / ('before-mysql-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.sqlite')
    try:
        with closing(sqlite3.connect(backup)) as copy:
            reader.backup(copy)
        # Use the immutable backup as the consistent source for the entire copy.
        reader.close()
        reader = sqlite3.connect(backup.as_uri() + '?mode=ro', uri=True)
        reader.row_factory = sqlite3.Row
        present = {r['name'] for r in reader.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {}
        with db.write() as conn:
            for table in TABLES:
                if conn.execute(f'SELECT COUNT(*) AS n FROM `{table}`').fetchone()['n']:
                    raise ValueError('MySQL 目标库已有数据，已停止迁移；请使用空库，避免覆盖现有数据')
            for table in TABLES:
                if table not in present:
                    continue
                rows = [dict(row) for row in reader.execute(f'SELECT * FROM `{table}`')]
                if rows:
                    cols = list(rows[0])
                    for column, _definition in db.COLUMNS.get(table, {}).items():
                        if column not in cols:
                            cols.append(column)
                            for row in rows:
                                row[column] = '' if column in ('permissions', 'group_id', 'status_override') else 0
                    if any(not c.replace('_', '').isalnum() for c in cols):
                        raise ValueError('源表包含不支持的列名')
                    sql = f"INSERT INTO `{table}` ({','.join('`'+c+'`' for c in cols)}) VALUES ({','.join('?' for _ in cols)})"
                    conn.executemany(sql, [tuple(row[c] for c in cols) for row in rows])
                actual = conn.execute(f'SELECT * FROM `{table}`').fetchall()
                # Check values as well as counts, including password hashes and timestamps.
                def normalized(values):
                    defaults = db.COLUMNS.get(table, {})
                    return sorted(json.dumps({k: v for k, v in dict(r).items() if k not in defaults},
                                             ensure_ascii=False, sort_keys=True) for r in values)
                if normalized(rows) != normalized(actual):
                    raise RuntimeError(f'{table} 数据校验失败，迁移已回滚')
                counts[table] = len(rows)
        return counts, backup
    finally:
        reader.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default=str(db.DB_PATH))
    args = parser.parse_args()
    counts, backup = migrate(args.source)
    print('迁移成功：' + '，'.join(f'{table}={count}' for table, count in counts.items()))
    print(f'SQLite 备份：{backup}')
