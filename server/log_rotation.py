"""Size-capped log files under data/logs/.

Runtime logs (such as the application's request log) would otherwise
grow without bound on a long-running deployment. Every file in the log directory is
checked and, once it passes LIMIT_BYTES, trimmed down to KEEP_BYTES by dropping the
oldest complete lines. Trimming runs from the application loop, so it costs nothing on
the request path.

The 查看日志 page is a different store: that is the operation_log table, trimmed by
`server.operation_log.prune` with the same 10 MB / 5 MB policy.
"""
from __future__ import annotations

import os

from server import db

LIMIT_BYTES = 10 * 1024 * 1024
KEEP_BYTES = 5 * 1024 * 1024


def directory():
    return db.DATA / 'logs'


def files():
    """Every regular log file currently present, newest name last."""
    base = directory()
    try:
        return sorted(path for path in base.iterdir() if path.is_file())
    except OSError:
        return []


def size():
    """Combined size of all log files, in bytes."""
    total = 0
    for path in files():
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def _trim(path, keep=None):
    """Rewrite `path` with its last `keep` bytes of complete lines, atomically.

    Writes a sibling temp file and renames it over the original, so a reader (or the
    process appending to it) never sees a half-written log. The rename is atomic on
    every platform the application supports. `keep` defaults to KEEP_BYTES; it is read
    at call time rather than bound as a default so the constant stays authoritative.
    """
    if keep is None:
        keep = KEEP_BYTES
    try:
        if path.stat().st_size <= keep:
            return 0
    except OSError:
        return 0
    temp = path.with_name(path.name + '.trim')
    try:
        with path.open('rb') as source:
            before = path.stat().st_size
            source.seek(-keep, os.SEEK_END)
            tail = source.read()
        # Drop the partial first line: keep only complete lines after the cut.
        cut = tail.find(b'\n')
        tail = b'' if cut < 0 else tail[cut + 1:]
        with temp.open('wb') as target:
            target.write(tail)
        os.replace(temp, path)
        return max(0, before - len(tail))
    except OSError:
        try:
            temp.unlink()
        except OSError:
            pass
        return 0


def prune():
    """Trim every log file over LIMIT_BYTES down to KEEP_BYTES. Returns bytes freed."""
    freed = 0
    for path in files():
        try:
            if path.stat().st_size > LIMIT_BYTES:
                freed += _trim(path)
        except OSError:
            continue
    return freed
