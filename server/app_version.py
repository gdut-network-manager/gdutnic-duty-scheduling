"""Deployment version; source archives can provide APP_VERSION or a VERSION file."""
import os
from pathlib import Path
import subprocess


def current_version(root: Path) -> str:
    configured = os.environ.get('APP_VERSION', '').strip()
    if configured:
        return configured
    version_file = root / 'VERSION'
    if version_file.is_file():
        value = version_file.read_text(encoding='utf-8').strip()
        if value:
            return value
    try:
        return subprocess.check_output(
            ['git', 'describe', '--tags', '--always', '--dirty'], cwd=root,
            text=True, stderr=subprocess.DEVNULL, timeout=2,
        ).strip() or '开发版（无版本标记）'
    except (OSError, subprocess.SubprocessError):
        return '开发版（无版本标记）'
