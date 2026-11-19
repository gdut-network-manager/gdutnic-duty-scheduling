"""Login throttling and response hardening shared by the login endpoints."""
from __future__ import annotations

import threading
import os
import ipaddress
import time
from collections import OrderedDict

# Cookie envelopes last four hours; server-side idle leases enforce inactivity limits.
SESSION_TTL = 4 * 60 * 60

# Failed attempts per key inside the window before the caller is throttled.
MAX_ATTEMPTS = 8
WINDOW_SECONDS = 10 * 60

# Duty accounts have a higher threshold. Throttle failed verifications only.
MEMBER_MAX_ATTEMPTS = 50
MEMBER_WINDOW_SECONDS = 5 * 60
MEMBER_IP_MAX_ATTEMPTS = 12


class AttemptLimiter:
    def __init__(self, max_attempts=MAX_ATTEMPTS, window=WINDOW_SECONDS):
        self.max_attempts = max_attempts
        self.window = window
        self._lock = threading.Lock()
        self._hits: OrderedDict[str, list[float]] = OrderedDict()
        self._blocked_until: dict[str, float] = {}

    def _recent(self, key):
        """Return stored hits, dropping anything older than the window."""
        now = time.monotonic()
        deadline = self._blocked_until.get(key)
        if deadline is not None and now >= deadline:
            self._blocked_until.pop(key, None)
            self._hits.pop(key, None)
        hits = self._hits.get(key)
        if not hits:
            return []
        fresh = [stamp for stamp in hits if now - stamp < self.window]
        if fresh:
            self._hits[key] = fresh
            self._hits.move_to_end(key)
        else:
            self._hits.pop(key, None)
        return fresh

    def blocked(self, key):
        with self._lock:
            self._recent(key)
            return time.monotonic() < self._blocked_until.get(key, 0)

    def fail(self, key):
        with self._lock:
            hits = self._recent(key)
            if time.monotonic() < self._blocked_until.get(key, 0):
                return False
            hits.append(time.monotonic())
            self._hits[key] = hits
            self._hits.move_to_end(key)
            throttled = len(hits) >= self.max_attempts
            if throttled:
                self._blocked_until[key] = time.monotonic() + self.window
            # Bound memory: only the newest few thousand keys are worth remembering.
            while len(self._hits) > 4096:
                removed, _ = self._hits.popitem(last=False)
                self._blocked_until.pop(removed, None)
            return throttled

    def reset(self, key):
        with self._lock:
            self._hits.pop(key, None)
            self._blocked_until.pop(key, None)


def login_account_key(role: str, name: str) -> str:
    """Bound key size while keeping case-sensitive account identities distinct."""
    import hashlib
    return f'{role}:account:' + hashlib.sha256(name.encode('utf-8')).hexdigest()


def client_key(request) -> str:
    """Use the single client address overwritten by a configured trusted proxy."""
    peer = request.client.host if request.client else "unknown"
    if not trusted_proxy(request):
        return peer
    header = os.environ.get("CLIENT_IP_HEADER", "").strip() or "X-Real-IP"
    values = request.headers.getlist(header)
    try:
        if len(values) != 1:
            return "unknown"
        address = ipaddress.ip_address(values[0].strip())
        return str(getattr(address, 'ipv4_mapped', None) or address)
    except ValueError:
        # Avoid treating the proxy's loopback address as the actual caller.
        return "unknown"


def trusted_proxy(request) -> bool:
    """Only configured socket peers may supply proxy headers."""
    try:
        address = ipaddress.ip_address(request.client.host if request.client else '')
        networks = [ipaddress.ip_network(value.strip(), strict=False)
                    for value in os.environ.get('LOGIN_TRUSTED_PROXIES', '').split(',') if value.strip()]
        return any(address in network for network in networks)
    except ValueError:
        return False


login_client_key = client_key


class IdentityCache:
    """Tiny per-thread cache for member identity checks.

    A page load issues several authenticated requests in a row; each one otherwise
    re-reads the member row. Entries are keyed by (name, password digest), so a
    password change or reset invalidates them without extra bookkeeping.
    """

    def __init__(self, ttl=10.0):
        self.ttl = ttl
        self._local = threading.local()

    def _store(self):
        store = getattr(self._local, 'entries', None)
        if store is None:
            store = self._local.entries = {}
        return store

    def get(self, key):
        entry = self._store().get(key)
        if not entry:
            return None
        stamp, value = entry
        if time.monotonic() - stamp > self.ttl:
            self._store().pop(key, None)
            return None
        return value

    def put(self, key, value):
        if not value:
            return  # never cache a rejection: deactivation must take effect immediately
        store = self._store()
        store[key] = (time.monotonic(), value)
        if len(store) > 512:
            oldest = min(store, key=lambda k: store[k][0])
            store.pop(oldest, None)

    def clear(self, prefix=''):
        store = self._store()
        if not prefix:
            store.clear()
            return
        for key in [k for k in store if k.startswith(prefix)]:
            store.pop(key, None)


MEMBER_SESSIONS = IdentityCache()
