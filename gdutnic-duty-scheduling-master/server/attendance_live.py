"""Attendance revision notifications, durable across processes and restarts."""
import asyncio
import time
import uuid
import weakref
from server import db

KEY = 'attendance_revision'
_waiters = weakref.WeakKeyDictionary()


def revision():
    with db.read() as conn:
        return str(db.get_setting(conn, KEY, '0'))


def publish(conn=None):
    value = uuid.uuid4().hex
    if conn is None:
        with db.write() as connection:
            db.set_setting(connection, KEY, value)
    else:
        db.set_setting(conn, KEY, value)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return value  # Other processes/threads are picked up by the durable revision.
    event = _waiters.pop(loop, None)
    if event:
        event.set()
    return value


async def wait(after, timeout=20):
    deadline = time.monotonic() + timeout
    loop = asyncio.get_running_loop()
    while True:
        # Register before reading, so publication between reads cannot be missed.
        event = _waiters.setdefault(loop, asyncio.Event())
        value = revision()
        remaining = deadline - time.monotonic()
        if value != after or remaining <= 0:
            return value
        try:
            await asyncio.wait_for(event.wait(), min(2, remaining))
        except asyncio.TimeoutError:
            pass
