"""System clock: either the real time or a manually pinned hour:minute for testing.

When manual mode is on, the pinned clock starts at hh:mm:00 and advances in real time
from that moment, so every module that asks for "now" sees a consistent shifted clock.
"""
from datetime import date, datetime, timedelta

from server import db

KEY = 'system_clock'


def _tz():
    from server import schedule_store
    return schedule_store.TZ


def _real_now():
    """Real time in Shanghai, resolved through schedule_store's clock."""
    from server import schedule_store
    return schedule_store.datetime.now(schedule_store.TZ)


def now_for(module_datetime=None):
    """`now()`, optionally reading real time from another module's datetime (tests)."""
    if module_datetime is None:
        return now()
    if not _loaded:
        load()
    real = module_datetime.now(_tz())
    if _offset is None:
        return real
    return real + timedelta(seconds=_offset)


def today():
    """Current date on the system clock."""
    return now().date()


TZ = None  # resolved lazily, see _tz()

# Frozen offset in seconds from real time; None means automatic.
_offset = None
_loaded = False


def _state():
    with db.read() as conn:
        return db.get_setting(conn, KEY, {}) or {}


def _apply(saved):
    """Turn a stored setting into a live offset."""
    global _offset
    if not isinstance(saved, dict) or saved.get('automatic', True):
        _offset = None
        return
    hour, minute = int(saved.get('hour', 0)), int(saved.get('minute', 0))
    started_at = str(saved.get('started_at') or '')
    now = _real_now().replace(tzinfo=None)
    try:
        start = datetime.fromisoformat(started_at)
        if start.tzinfo is not None:
            start = start.astimezone(_tz()).replace(tzinfo=None)
    except ValueError:
        start = now
    start = start.replace(microsecond=0)
    # The pinned date defaults to the day the setting was saved.
    day = str(saved.get('date') or '')
    try:
        pinned_day = datetime.fromisoformat(day).date() if day else start.date()
    except ValueError:
        pinned_day = start.date()
    pinned = datetime.combine(pinned_day, datetime.min.time(),
                              tzinfo=None).replace(hour=hour, minute=minute)
    # Offset is fixed at save time; `now()` adds real elapsed time, so the pinned clock
    # starts at hh:mm:00 and keeps ticking like a normal clock.
    _offset = (pinned - start).total_seconds()


def load():
    """Read the stored clock once per process (and after every save)."""
    global _loaded
    _apply(_state())
    _loaded = True
    return public()


def now():
    if not _loaded:
        load()
    real = _real_now()
    if _offset is None:
        return real
    return real + timedelta(seconds=_offset)


def public():
    current = now()
    return {'automatic': _offset is None, 'current': current.isoformat(timespec='seconds'),
            **_settings_echo()}


def _settings_echo():
    saved = _state()
    if _offset is not None:
        current = now()
        return {'date': current.date().isoformat(), 'hour': int(saved.get('hour', 0)),
                'minute': int(saved.get('minute', 0))}
    current = _real_now()
    return {'date': current.date().isoformat(), 'hour': current.hour, 'minute': current.minute}


def save(automatic=True, hour=0, minute=0, day=None):
    """Pin the clock to `day` (default today) at hour:minute:00, or return to real time."""
    global _offset
    if not automatic:
        hour, minute = int(hour), int(minute)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError('小时需为 0–23，分钟需为 0–59')
        try:
            pinned_day = date.fromisoformat(str(day)) if day else _real_now().date()
        except ValueError as exc:
            raise ValueError('日期格式应为 YYYY-MM-DD') from exc
    else:
        hour = minute = 0
        pinned_day = None
    saved = {'automatic': bool(automatic), 'hour': hour, 'minute': minute}
    if pinned_day is not None:
        saved['date'] = pinned_day.isoformat()
    if not automatic:
        # The pinned clock starts at hh:mm:00 right now and runs from there.
        saved['started_at'] = _real_now().isoformat(timespec='seconds')
    with db.write() as conn:
        db.set_setting(conn, KEY, saved)
    _apply(saved)
    return public()
