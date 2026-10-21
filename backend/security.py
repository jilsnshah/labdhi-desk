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

import base64
import hashlib
import hmac
import os
import time

from fastapi import Request
from fastapi.responses import JSONResponse

HEADER = "x-labdhi-token"
COOKIE = "labdhi_device"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}

# How long a device stays unlocked. A trader opens the desk from his own phone
# and his own laptop; being asked for the password on every visit is friction
# with nothing to show for it, since anyone holding the device could read the
# password out of the browser anyway.
DEVICE_DAYS = 180


def token() -> str:
    # LABDHI_TOKEN is the older name for the same secret; both are accepted.
    return (os.environ.get("LABDHI_PASSWORD") or os.environ.get("LABDHI_TOKEN") or "").strip()


# ------------------------------------------------------------------ device
# The password is typed once per device. What is kept afterwards is not the
# password but a signature over an issue date, so:
#   - nothing readable is stored on the device, and the cookie is HttpOnly, so
#     a script cannot read it either;
#   - changing LABDHI_PASSWORD signs every device out at once;
#   - an old cookie stops working on its own after DEVICE_DAYS.
#
# This sits beside the header the app sends from localStorage, not instead of
# it: phones clear local storage on their own (iOS does after a week unused),
# and a cookie survives that.


def _sign(issued: str) -> str:
    mac = hmac.new(token().encode(), issued.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def device_cookie() -> str:
    issued = str(int(time.time()))
    return "%s.%s" % (issued, _sign(issued))


def _device_ok(value: str) -> bool:
    issued, _, mac = (value or "").partition(".")
    if not issued.isdigit() or not mac:
        return False
    if not hmac.compare_digest(mac, _sign(issued)):
        return False
    return time.time() - int(issued) < DEVICE_DAYS * 86400


_FAILS: dict = {}
_WINDOW = 300.0      # seconds
_MAX_FAILS = 12


def _throttled(host: str) -> bool:
    now = time.time()
    hits = [t for t in _FAILS.get(host, []) if now - t < _WINDOW]
    _FAILS[host] = hits
    return len(hits) >= _MAX_FAILS


def _record_fail(host: str) -> None:
    _FAILS.setdefault(host, []).append(time.time())


def _is_local(request: Request) -> bool:
    """Strictly the socket, never a header.

    _host() below believes X-Forwarded-For, which is right for counting failed
    attempts and wrong here: anyone can claim to be 127.0.0.1, and this is the
    check that decides whether a desk with no password set is served at all.
    """
    return ((request.client.host if request.client else "") or "") in LOCAL_HOSTS


# Behind Cloud Run the socket comes from the platform's proxy, not the trader.
# Taking the address from the socket would put every device on earth into one
# throttle bucket, so the forwarded address is used where there is one.
def _host(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return (request.client.host if request.client else "") or ""


def _https(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", "")
    if proto:
        return proto.split(",")[0].strip() == "https"
    return request.url.scheme == "https"


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

    host = _host(request) or "?"

    # Already a known device: nothing to type. Checked before the throttle,
    # because a signed cookie is proof rather than a guess - otherwise someone
    # else behind the same office connection mistyping the password would shut
    # the trader out of his own phone.
    if _device_ok(request.cookies.get(COOKIE, "")):
        _FAILS.pop(host, None)
        return await call_next(request)

    if _throttled(host):
        return JSONResponse(status_code=429,
                            content={"error": "Too many attempts. Try again in a few minutes."})

    given = request.headers.get(HEADER, "")
    if not hmac.compare_digest(given, secret):
        _record_fail(host)
        return JSONResponse(status_code=401, content={"error": "Wrong password"})
    _FAILS.pop(host, None)

    # The password was right, so this device is now a known one. Remembering it
    # here rather than in the app means it survives a phone clearing its local
    # storage, which iOS does on its own after a week unused.
    response = await call_next(request)
    remember(response, secure=_https(request))
    return response


def remember(response, secure: bool = True) -> None:
    response.set_cookie(
        COOKIE, device_cookie(), max_age=DEVICE_DAYS * 86400, path="/",
        httponly=True, secure=secure, samesite="lax",
    )


def forget(response) -> None:
    response.delete_cookie(COOKIE, path="/")
