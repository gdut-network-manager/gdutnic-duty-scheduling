import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from server import db, security


class LimiterTest(unittest.TestCase):
    def test_blocks_after_repeated_failures_and_resets(self):
        limiter = security.AttemptLimiter(max_attempts=3, window=60)
        for _ in range(3):
            self.assertFalse(limiter.blocked('ip'))
            limiter.fail('ip')
        self.assertTrue(limiter.blocked('ip'))
        limiter.reset('ip')
        self.assertFalse(limiter.blocked('ip'))

    def test_window_expires_old_attempts(self):
        limiter = security.AttemptLimiter(max_attempts=2, window=10)
        # security.py imports the time module, so patching monotonic affects both calls.
        with patch.object(security.time, 'monotonic', return_value=100.0):
            limiter.fail('ip')
            limiter.fail('ip')
            self.assertTrue(limiter.blocked('ip'))
        with patch.object(security.time, 'monotonic', return_value=111.0):
            self.assertFalse(limiter.blocked('ip'))

    def test_keys_are_isolated_and_memory_is_bounded(self):
        limiter = security.AttemptLimiter(max_attempts=1, window=60)
        limiter.fail('a')
        self.assertTrue(limiter.blocked('a'))
        self.assertFalse(limiter.blocked('b'))
        limiter.max_attempts = 1
        for i in range(5000):
            limiter.fail(f'k{i}')
        self.assertLessEqual(len(limiter._hits), 4096)

    def test_session_ttl_is_four_hours(self):
        self.assertEqual(security.SESSION_TTL, 4 * 60 * 60)

    def test_lockout_lasts_ten_minutes_from_threshold(self):
        limiter = security.AttemptLimiter()
        with patch.object(security.time, 'monotonic', return_value=100.0):
            for _ in range(7):
                limiter.fail('admin')
        with patch.object(security.time, 'monotonic', return_value=200.0):
            limiter.fail('admin')
            self.assertTrue(limiter.blocked('admin'))
        with patch.object(security.time, 'monotonic', return_value=799.0):
            self.assertTrue(limiter.blocked('admin'))
            limiter.fail('admin')  # Blocked requests must not extend the deadline.
        with patch.object(security.time, 'monotonic', return_value=800.0):
            self.assertFalse(limiter.blocked('admin'))
            limiter.fail('admin')
            self.assertFalse(limiter.blocked('admin'))
