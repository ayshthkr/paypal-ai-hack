"""Bounded private response cache with conditional requests and write invalidation."""
import hashlib
import time
from collections import OrderedDict
from threading import RLock

CACHE_CONTROL = b"private, max-age=30, stale-while-revalidate=120"
_entries = OrderedDict()
_lock = RLock()
_generation = 0


def invalidate():
    global _generation
    with _lock:
        _generation += 1
        _entries.clear()
        _aggregates.clear()


class PrivateReadCache:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        method, path = scope["method"], scope["path"]
        headers = dict(scope["headers"])
        eligible = method == "GET" and path not in {"/health", "/ready", "/auth/me"} and not path.endswith(".csv")
        if method == "GET" and not eligible:
            return await self.app(scope, receive, send)
        from starlette.requests import Request
        from app.security import session_user
        user = session_user(Request(scope))
        key = (user, path, scope["query_string"], hashlib.sha256(headers.get(b"cookie", b"")).digest())
        with _lock:
            generation = _generation
            entry = _entries.get(key) if eligible else None
            if entry and entry[0] < time.monotonic():
                _entries.pop(key, None)
                entry = None
        if entry:
            _, status, response_headers, body, etag = entry
            conditional = headers.get(b"if-none-match", b"")
            status = 304 if etag in [value.strip() for value in conditional.split(b",")] else status
            response_headers = [(k, v) for k, v in response_headers if k not in {b"server-timing", b"content-length"}]
            response_headers.append((b"server-timing", b"cache;dur=0;desc=hit"))
            await send({"type": "http.response.start", "status": status, "headers": response_headers})
            return await send({"type": "http.response.body", "body": b"" if status == 304 else body})
        messages = []
        async def capture(message):
            messages.append(message)
        await self.app(scope, receive, capture)
        if method not in {"GET", "HEAD", "OPTIONS"}:
            invalidate()
        start = next((m for m in messages if m["type"] == "http.response.start"), None)
        if eligible and start and start["status"] == 200:
            body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
            etag = b'"' + hashlib.sha256(body).hexdigest().encode() + b'"'
            response_headers = [(k, v) for k, v in start["headers"] if k not in {b"cache-control", b"etag", b"vary"}]
            response_headers.extend([(b"cache-control", CACHE_CONTROL), (b"etag", etag), (b"vary", b"Cookie")])
            start["headers"] = response_headers
            with _lock:
                if generation == _generation and len(body) < 2_000_000:
                    _entries[key] = (time.monotonic() + 30, 200, response_headers, body, etag)
                    while len(_entries) > 128:
                        _entries.popitem(last=False)
        for message in messages:
            await send(message)


_aggregates = OrderedDict()


def cached_aggregate(key, factory):
    with _lock:
        generation = _generation
        entry = _aggregates.get((generation, key))
        if entry and entry[0] > time.monotonic():
            return entry[1]
    value = factory()
    with _lock:
        if generation == _generation:
            _aggregates[(generation, key)] = (time.monotonic()+30, value)
            while len(_aggregates) > 32:
                _aggregates.popitem(last=False)
    return value
