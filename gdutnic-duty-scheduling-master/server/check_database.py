"""Check database availability before starting the application server."""
from __future__ import annotations

import os
import sys

from server import db


def main() -> int:
    try:
        backend = db.backend()
        with db.read() as conn:
            conn.execute('SELECT 1').fetchone()
    except Exception as exc:
        if isinstance(exc, RuntimeError) and exc.__cause__ is not None:
            cause = exc.__cause__
        else:
            cause = exc
        if os.environ.get('DB_BACKEND', 'mysql').lower() == 'mysql':
            host = os.environ.get('MYSQL_HOST', '127.0.0.1')
            port = os.environ.get('MYSQL_PORT', '3306')
            code = cause.args[0] if cause.args else None
            print(f'[start] MySQL 检查失败：{host}:{port}（错误码 {code if isinstance(code, int) else "未知"}）。', file=sys.stderr)
            if code in (2002, 2003):
                print('[start] 无法连接数据库，请检查数据库服务、端口和网络。', file=sys.stderr)
                if host in ('127.0.0.1', 'localhost', '::1'):
                    print('[start] 使用项目自带 MySQL 时，先启动 Docker Desktop / Docker 服务，再在项目目录执行：', file=sys.stderr)
                    print('  docker compose --env-file data/.env up -d --wait mysql', file=sys.stderr)
            elif code == 1045:
                print('[start] 数据库拒绝认证，请核对 data/.env 的 MYSQL_USER / MYSQL_PASSWORD 与现有数据库账号。', file=sys.stderr)
            elif code == 1049:
                print('[start] 数据库不存在，请核对 MYSQL_DATABASE 并先创建数据库。', file=sys.stderr)
            else:
                print('[start] 请检查 data/.env 的 MYSQL_* 配置、TLS 证书及数据库日志。', file=sys.stderr)
        else:
            print(f'[start] 数据库检查失败（{type(cause).__name__}），请检查数据目录和数据库文件权限。', file=sys.stderr)
        return 1
    finally:
        if db._conn is not None:
            db._conn.close()
            db._conn = None
    print(f'[start] {backend} 数据库检查通过。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
