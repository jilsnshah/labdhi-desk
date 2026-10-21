"""The password is typed once per device, and then not again.

A trader opens the desk twenty times a day from the same phone. Asking for the
password every time taught him to keep it somewhere worse than the browser
does, so a correct password now leaves a signed cookie behind and the device is
known after that. These tests hold the lock itself to account: the cookie must
be honoured, must not be forgeable, must die with the password and must expire.

The guard is ASGI middleware, so each test drives it with a request built by
hand rather than over a socket.
"""
import asyncio
import base64
import hashlib
import hmac
import os
import time
import unittest

from starlette.requests import Request
from starlette.responses import JSONResponse

from backend import security

PASSWORD = "12345"


def run(method="GET", path="/api/summary", headers=None, cookies=None, scheme="https",
        client="49.37.1.1"):
    """Push one request through the guard and hand back the response."""
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if cookies:
        raw.append((b"cookie", "; ".join("%s=%s" % kv for kv in cookies.items()).encode()))
    scope = {
        "type": "http", "method": method, "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": raw, "scheme": scheme, "server": ("desk", 443),
        "client": (client, 51515), "root_path": "", "app": None,
    }

    async def call_next(_request):
        return JSONResponse({"ok": True})

    return asyncio.get_event_loop().run_until_complete(
        security.guard(Request(scope), call_next))


def cookie_of(response):
    for key, value in response.raw_headers:
        if key.lower() == b"set-cookie":
            return value.decode()
    return ""


class Lock(unittest.TestCase):
    def setUp(self):
        self.before = os.environ.get("LABDHI_PASSWORD")
        os.environ["LABDHI_PASSWORD"] = PASSWORD
        security._FAILS.clear()

    def tearDown(self):
        if self.before is None:
            os.environ.pop("LABDHI_PASSWORD", None)
        else:
            os.environ["LABDHI_PASSWORD"] = self.before
        security._FAILS.clear()

    # ------------------------------------------------------------ the password
    def test_no_password_is_refused(self):
        self.assertEqual(run().status_code, 401)

    def test_a_wrong_password_is_refused(self):
        self.assertEqual(run(headers={security.HEADER: "wrong"}).status_code, 401)

    def test_the_right_password_opens_the_book_and_is_remembered(self):
        response = run(headers={security.HEADER: PASSWORD})
        self.assertEqual(response.status_code, 200)
        planted = cookie_of(response)
        self.assertIn(security.COOKIE, planted)
        self.assertIn("HttpOnly", planted)          # a script cannot read it
        self.assertIn("Secure", planted)            # https only
        self.assertIn("SameSite=lax", planted.replace("samesite", "SameSite"))
        self.assertIn("Max-Age=%d" % (security.DEVICE_DAYS * 86400), planted)

    def test_on_a_laptop_over_plain_http_the_cookie_is_not_marked_secure(self):
        planted = cookie_of(run(headers={security.HEADER: PASSWORD}, scheme="http"))
        self.assertNotIn("Secure", planted)

    # ------------------------------------------------------------ the device
    def test_a_known_device_needs_no_password(self):
        response = run(cookies={security.COOKIE: security.device_cookie()})
        self.assertEqual(response.status_code, 200)

    def test_a_forged_cookie_is_refused(self):
        for value in ["", "rubbish", "0.0", "abc.def",
                      "%d.%s" % (int(time.time()), "not-the-signature"),
                      str(int(time.time()))]:
            self.assertEqual(run(cookies={security.COOKIE: value}).status_code, 401, value)

    def test_a_cookie_signed_with_another_password_is_refused(self):
        theirs = security.device_cookie()
        os.environ["LABDHI_PASSWORD"] = "54321"
        self.assertEqual(run(cookies={security.COOKIE: theirs}).status_code, 401)

    def test_changing_the_password_signs_every_device_out(self):
        mine = security.device_cookie()
        self.assertEqual(run(cookies={security.COOKIE: mine}).status_code, 200)
        os.environ["LABDHI_PASSWORD"] = "a new one"
        self.assertEqual(run(cookies={security.COOKIE: mine}).status_code, 401)

    def test_a_cookie_older_than_the_window_is_refused(self):
        issued = str(int(time.time()) - (security.DEVICE_DAYS + 1) * 86400)
        mac = hmac.new(PASSWORD.encode(), issued.encode(), hashlib.sha256).digest()
        stale = "%s.%s" % (issued, base64.urlsafe_b64encode(mac).decode().rstrip("="))
        self.assertEqual(run(cookies={security.COOKIE: stale}).status_code, 401)

    def test_a_cookie_just_inside_the_window_still_works(self):
        issued = str(int(time.time()) - (security.DEVICE_DAYS - 1) * 86400)
        mac = hmac.new(PASSWORD.encode(), issued.encode(), hashlib.sha256).digest()
        fresh = "%s.%s" % (issued, base64.urlsafe_b64encode(mac).decode().rstrip("="))
        self.assertEqual(run(cookies={security.COOKIE: fresh}).status_code, 200)

    # ------------------------------------------------------------ the rest
    def test_health_is_open_and_static_files_are_untouched(self):
        self.assertEqual(run(path="/api/health").status_code, 200)
        self.assertEqual(run(path="/js/app.js").status_code, 200)
        self.assertEqual(run(path="/").status_code, 200)

    def test_guessing_is_throttled(self):
        for _ in range(security._MAX_FAILS):
            run(headers={security.HEADER: "wrong"})
        self.assertEqual(run(headers={security.HEADER: "wrong"}).status_code, 429)
        self.assertEqual(run(headers={security.HEADER: PASSWORD}).status_code, 429)
        # another address is unaffected
        self.assertEqual(run(headers={security.HEADER: PASSWORD}, client="1.2.3.4").status_code, 200)

    def test_a_known_device_is_not_shut_out_by_someone_else_guessing(self):
        """One office connection, one scanner, and the trader still gets in."""
        known = security.device_cookie()
        for _ in range(security._MAX_FAILS + 5):
            run(headers={security.HEADER: "wrong"})
        self.assertEqual(run(headers={security.HEADER: PASSWORD}).status_code, 429)
        self.assertEqual(run(cookies={security.COOKIE: known}).status_code, 200)

    def test_without_a_password_set_only_this_machine_is_served(self):
        os.environ.pop("LABDHI_PASSWORD", None)
        self.assertEqual(run().status_code, 503)
        self.assertEqual(run(client="127.0.0.1").status_code, 200)

    def test_claiming_to_be_localhost_does_not_work(self):
        """X-Forwarded-For is whatever the caller types; it cannot open a desk."""
        os.environ.pop("LABDHI_PASSWORD", None)
        self.assertEqual(run(headers={"x-forwarded-for": "127.0.0.1"},
                             client="203.0.113.9").status_code, 503)

    # ------------------------------------------------------------ behind a proxy
    def test_the_trader_is_told_apart_from_the_platform_proxy(self):
        """Cloud Run's socket is the proxy's; the throttle must follow the device.

        Otherwise one scanner puts every phone on earth in the same bucket.
        """
        proxy = "169.254.1.1"
        for _ in range(security._MAX_FAILS):
            run(headers={security.HEADER: "wrong", "x-forwarded-for": "203.0.113.9"}, client=proxy)
        self.assertEqual(run(headers={security.HEADER: "wrong", "x-forwarded-for": "203.0.113.9"},
                             client=proxy).status_code, 429)
        self.assertEqual(run(headers={security.HEADER: PASSWORD, "x-forwarded-for": "203.0.113.10"},
                             client=proxy).status_code, 200)

    def test_https_is_read_from_the_proxy_header(self):
        planted = cookie_of(run(headers={security.HEADER: PASSWORD, "x-forwarded-proto": "https"},
                                scheme="http"))
        self.assertIn("Secure", planted)

    def test_forgetting_this_device_clears_the_cookie(self):
        response = JSONResponse({"ok": True})
        security.forget(response)
        self.assertIn(security.COOKIE, cookie_of(response))
        self.assertIn("Max-Age=0", cookie_of(response))


if __name__ == "__main__":
    unittest.main(verbosity=2)
