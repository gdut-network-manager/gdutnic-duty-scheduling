"""Prepare ignored runtime files without connecting to or replacing a database."""
from pathlib import Path
import secrets
import shutil
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def initialize(root: Path = ROOT) -> Path:
    data = root / 'data'
    data.mkdir(parents=True, exist_ok=True)
    # Preserve credentials when upgrading an existing checkout.
    old_env, env = root / '.env', data / '.env'
    if old_env.exists():
        if env.exists() and old_env.read_bytes() != env.read_bytes():
            raise RuntimeError('根目录 .env 与 data/.env 不一致，请先合并配置；未覆盖任何文件')
        if not env.exists():
            shutil.move(str(old_env), env)
        else:
            old_env.unlink()
    if not env.exists():
        template = (root / '.env.example').read_text(encoding='utf-8')
        template = template.replace('replace-with-your-database-password', secrets.token_hex(32))
        template = template.replace('replace-with-a-different-root-password', secrets.token_hex(32))
        try:
            with env.open('x', encoding='utf-8') as stream:
                stream.write(template)
        except FileExistsError:
            pass
    env.chmod(0o600)
    for name in ('imports', 'backups', 'logs', 'mysql', 'certs'):
        (data / name).mkdir(exist_ok=True)
    # The 原始管理员's credentials live in this one file, never in the database.
    root_file = data / 'root_admin.json'
    if root_file.exists():
        root_file.chmod(0o600)
    # MySQL must agree with the host filesystem's case sensitivity, including on macOS.
    config_dir = data / 'mysql-config'
    config_dir.mkdir(exist_ok=True)
    config = config_dir / 'runtime.cnf'
    if not config.exists():
        with tempfile.TemporaryDirectory(prefix='case-probe-', dir=data) as probe:
            marker = Path(probe) / 'CaseProbe'
            marker.touch()
            insensitive = (Path(probe) / 'caseprobe').exists()
        try:
            with config.open('x', encoding='utf-8') as stream:
                stream.write('[mysqld]\nlower_case_table_names=' + ('2' if insensitive else '0') + '\n')
        except FileExistsError:
            pass
    return data


if __name__ == '__main__':
    initialize()
    print('[data] 已准备 data/ 目录与配置；现有数据库和配置保持原样。')
