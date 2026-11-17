#!/usr/bin/env python3
"""The one place the loader talks HTTP to Workfront (Core and Planning). Standard library only.

Rules:
  * The secret is added to the request header here and nowhere else. It never appears in a URL, a message, a log or a report: every error text is passed through
    loadrun.scrub() and then through loadconfig.redact() with every configured secret.
  * A reply is JSON or nothing. A redirect is NOT followed (a redirect could carry the secret to another host); it is reported as an error.
  * A busy server (429, 502, 503, 504) or a dropped connection is tried again a few times with a growing wait. A refusal (400, 401, 403, 404, 409) is not tried again.
  * The caller can give its own `opener` and `sleep`, so tests run without a network and without waiting.
"""
import json, os, time, urllib.error, urllib.parse, urllib.request, uuid

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


def uuid_hex():
    return uuid.uuid4().hex                    # only a multipart boundary: it never reaches stored data, so it does not affect determinism of any output


class _Multipart(object):
    """A file-like body: the part header, the file read in chunks, the closing boundary. urllib reads it with read(n)."""
    def __init__(self, head, fh, tail):
        self.parts, self.fh = [head], fh
        self.tail, self.state = tail, 0
        self.buf = head

    def read(self, n=-1):
        out = b""
        want = n if n is not None and n >= 0 else float("inf")
        while len(out) < want:
            if self.buf:
                take = self.buf if want == float("inf") else self.buf[:want - len(out)]
                self.buf = self.buf[len(take):]
                out += take
                continue
            if self.state == 0:
                chunk = self.fh.read(1 << 20 if want == float("inf") else max(1, want - len(out)))
                if chunk:
                    self.buf = chunk
                else:
                    self.state, self.buf = 1, self.tail
            else:
                break
        return out


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

    def upload(self, path, field, filename, file_path, content_type="application/octet-stream", query=None):
        """POST one file as multipart/form-data, streamed from disk (never read whole into memory). Returns the parsed JSON reply. The file name goes to Workfront as the
        part's file name and nowhere else. Same rules as request(): secret only in the header, no redirect followed, a busy server is retried (the file is opened again)."""
        name, value = self.creds.authorization_header()
        boundary = "----migrationtool" + uuid_hex()
        crlf = chr(13) + chr(10)
        safe = "".join(" " if ord(ch) < 32 else ch for ch in str(filename or "file")).replace('"', "")
        head = crlf.join(["--" + boundary, 'Content-Disposition: form-data; name="%s"; filename="%s"' % (field, safe), "Content-Type: " + content_type, "", ""]).encode("utf-8")
        tail = (crlf + "--" + boundary + "--" + crlf).encode("utf-8")
        size = os.path.getsize(file_path)
        last = None
        for attempt in range(len(WAITS) + 1):
            with open(file_path, "rb") as fh:
                req = urllib.request.Request(self.url(path, query), data=_Multipart(head, fh, tail), method="POST",
                                             headers={"Content-Type": "multipart/form-data; boundary=" + boundary, "Content-Length": str(len(head) + size + len(tail)), "Accept": "application/json", name: value})
                self.calls += 1
                try:
                    with self.opener.open(req, timeout=self.timeout) as r:
                        raw, status = r.read(), r.status
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
