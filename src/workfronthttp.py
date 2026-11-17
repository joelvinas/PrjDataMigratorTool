#!/usr/bin/env python3
"""The one place the loader talks HTTP to Workfront (Core and Planning). Standard library only.

Rules:
  * The secret is added to the request header here and nowhere else. It never appears in a URL, a message, a log or a report: every error text is passed through
    loadrun.scrub() and then through loadconfig.redact() with every configured secret.
  * A reply is JSON or nothing. A redirect is NOT followed (a redirect could carry the secret to another host); it is reported as an error.
  * A busy server (429, 502, 503, 504) or a dropped connection is tried again a few times with a growing wait. A refusal (400, 401, 403, 404, 409) is not tried again.
  * The caller can give its own `opener` and `sleep`, so tests run without a network and without waiting.
"""
import json, time, urllib.error, urllib.parse, urllib.request

import loadconfig as lc
import loadrun as lr

RETRY_STATUS = (429, 502, 503, 504)
WAITS = (1, 2, 4)           # seconds before the 2nd, 3rd and 4th try


class HttpError(Exception):
    """A failed call. `status` is the HTTP status (None when nothing came back). `text` is already cleaned of any secret."""
    def __init__(self, message, status=None):
        super(HttpError, self).__init__(message)
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class Http(object):
    def __init__(self, creds, secrets=(), opener=None, sleep=time.sleep, timeout=60):
        self.creds, self.secrets, self.sleep, self.timeout = creds, set(secrets) | {creds.secret()}, sleep, timeout
        self.opener = opener or urllib.request.build_opener(_NoRedirect)
        self.calls = 0

    def clean(self, text):
        return lc.redact(lr.scrub(text, 400), self.secrets)

    def url(self, path, query=None):
        base = self.creds.base_url + (self.creds.api_path or "")
        return base + path + (("?" + urllib.parse.urlencode(sorted(query.items()))) if query else "")

    def request(self, method, path, body=None, query=None):
        """Send one call. Returns the parsed JSON reply (None for an empty one). Raises HttpError with a plain, cleaned message."""
        name, value = self.creds.authorization_header()
        data = None if body is None else json.dumps(body).encode("utf-8")
        last = None
        for attempt in range(len(WAITS) + 1):
            req = urllib.request.Request(self.url(path, query), data=data, method=method, headers={"Content-Type": "application/json", "Accept": "application/json", name: value})
            self.calls += 1
            try:
                with self.opener.open(req, timeout=self.timeout) as r:
                    raw = r.read()
                    status = r.status
            except urllib.error.HTTPError as e:
                status, raw = e.code, e.read()
                if 300 <= status < 400:
                    raise HttpError("The server answered with a redirect (%d), which is not followed so the credentials stay with the configured address." % status, status)
                if status not in RETRY_STATUS:
                    raise HttpError(self._failure(status, raw), status)
                last = HttpError(self._failure(status, raw), status)
            except (urllib.error.URLError, OSError) as e:
                last = HttpError(self.clean("Could not reach the server: %s" % getattr(e, "reason", e)), None)
            else:
                if not raw.strip():
                    return None
                try:
                    return json.loads(raw.decode("utf-8"))
                except ValueError:
                    raise HttpError("The server's answer (HTTP %d) was not JSON." % status, status)
            if attempt < len(WAITS):
                self.sleep(WAITS[attempt])
        raise last

    def _failure(self, status, raw):
        """HTTP status plus a short, cleaned excerpt of the error body (the error code is what a person needs)."""
        try:
            text = raw.decode("utf-8", "replace")
        except Exception:
            text = ""
        return self.clean("HTTP %d: %s" % (status, " ".join(text.split())))
