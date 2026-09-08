"""Access control for a deployed desk.

Locally there is nothing to protect: the server binds to 127.0.0.1 and only the
trader can reach it. The moment it is on a public URL that stops being true —
every endpoint here can read the whole book, book deals and cancel them, and
none of that should be one guessed hostname away.

So: if LABDHI_PASSWORD is set, every /api call must carry it. If it is not set,
the app refuses to serve anything but localhost, rather than silently running
wide open somewhere public.

The password is a single shared secret, so a short one is worth exactly what it
sounds like. Failed attempts are throttled per address below, which slows a
scanner down but cannot save a password that someone can simply guess.
"""
from __future__ import annotations

import hmac
import os

from fastapi import Request
from fastapi.responses import JSONResponse

HEADER = "x-labdhi-token"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}


def token() -> str:
    # LABDHI_TOKEN is the older name for the same secret; both are accepted.
    return (os.environ.get("LABDHI_PASSWORD") or os.environ.get("LABDHI_TOKEN") or "").strip()


_FAILS: dict = {}
_WINDOW = 300.0      # seconds
_MAX_FAILS = 12


def _throttled(host: str) -> bool:
    import time
    now = time.time()
    hits = [t for t in _FAILS.get(host, []) if now - t < _WINDOW]
    _FAILS[host] = hits
    return len(hits) >= _MAX_FAILS


def _record_fail(host: str) -> None:
    import time
    _FAILS.setdefault(host, []).append(time.time())


def _is_local(request: Request) -> bool:
    host = (request.client.host if request.client else "") or ""
    return host in LOCAL_HOSTS


async def guard(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/") or path == "/api/health":
        return await call_next(request)

    secret = token()
    if not secret:
        # No token configured: fine on a laptop, never on the open internet.
        if _is_local(request):
            return await call_next(request)
        return JSONResponse(
            status_code=503,
            content={"error": "This deployment has no LABDHI_TOKEN set, so it will not "
                              "serve remote requests. Set one and restart."},
        )

    host = (request.client.host if request.client else "") or "?"
    if _throttled(host):
        return JSONResponse(status_code=429,
                            content={"error": "Too many attempts. Try again in a few minutes."})

    given = request.headers.get(HEADER, "")
    if not hmac.compare_digest(given, secret):
        _record_fail(host)
        return JSONResponse(status_code=401, content={"error": "Wrong password"})
    _FAILS.pop(host, None)
    return await call_next(request)
