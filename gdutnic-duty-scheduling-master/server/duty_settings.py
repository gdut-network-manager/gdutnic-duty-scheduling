"""Attendance window settings: early check-in, lateness and absence thresholds."""
from server import db

KEY = 'duty_rules'
DEFAULTS = {'early_minutes': 10, 'late_minutes': 15, 'absent_minutes': 30}


def _clamp(value, low, high, default):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(number, high))


def _normalize(body):
    body = body if isinstance(body, dict) else {}
    return {
        'early_minutes': _clamp(body.get('early_minutes'), 0, 120, DEFAULTS['early_minutes']),
        'late_minutes': _clamp(body.get('late_minutes'), 0, 240, DEFAULTS['late_minutes']),
        'absent_minutes': _clamp(body.get('absent_minutes'), 1, 480, DEFAULTS['absent_minutes']),
    }


def load():
    with db.read() as conn:
        saved = db.get_setting(conn, KEY)
    rules = _normalize(saved)
    # Legacy rows may predate the ordering rule; keep the absence window usable.
    if rules['absent_minutes'] <= rules['late_minutes']:
        rules['absent_minutes'] = min(480, rules['late_minutes'] + 1)
    return rules


def save(body):
    rules = _normalize(body)
    # Late and absence are cumulative from the shift start; absence must come after late.
    if rules['absent_minutes'] <= rules['late_minutes']:
        raise ValueError('旷班时间必须大于迟到时间，两者都从班次开始时间起算')
    with db.write() as conn:
        db.set_setting(conn, KEY, rules)
    return rules
