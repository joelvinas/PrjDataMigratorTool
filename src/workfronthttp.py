#!/usr/bin/env python3
"""The one place the loader talks HTTP to Workfront (Core and Planning). Standard library only.

Rules:
  * The secret is added to the request header here and nowhere else. It never appears in a URL, a message, a log or a report: every error text is passed through
    loadrun.scrub() and then through loadconfig.redact() with every configured secret.
  * A reply is JSON or nothing. A redirect is NOT followed (a redirect could carry the secret to another host); it is reported as an error.
  * A busy server (429, 502, 503, 504) or a dropped connection is tried again a few times with a growing wait. A refusal (400, 401, 403, 404, 409) is not tried again.
  * The caller can give its own `opener` and `sleep`, so tests run without a network and without waiting.
"""
import json, os, re, time, urllib.error, urllib.parse, urllib.request, uuid, zlib

import loadconfig as lc
import loadrun as lr

RETRY_STATUS = (429, 502, 503, 504)
WAITS = (1, 2, 4)           # seconds before the 2nd, 3rd and 4th try


MAX_BODY = 1 << 20          # a reply is read no further than this once decompressed (a small error text is all that is ever needed)


def read_text(raw, headers=None):
    """(text, readable). A reply body turned into text without ever showing replacement or control characters: it is un-compressed when the server compressed it
    anyway, decoded as the Content-Type's charset (else UTF-8, a UTF-16 byte order mark, then Windows-1252), and judged unreadable when it is still mostly binary."""
    get = (lambda k: headers.get(k)) if headers is not None and hasattr(headers, "get") else (lambda k: None)
    enc = str(get("Content-Encoding") or "").strip().lower()
    body = raw or b""
    try:
        if enc in ("gzip", "x-gzip"):
            body = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(body, MAX_BODY)
        elif enc == "deflate":
            try:
                body = zlib.decompressobj().decompress(body, MAX_BODY)
            except zlib.error:
                body = zlib.decompressobj(-zlib.MAX_WBITS).decompress(body, MAX_BODY)
    except zlib.error:
        pass                                    # not really compressed after all: read it as it came
    m = re.search(r"charset\s*=\s*[\"']?([A-Za-z0-9_.:-]+)", str(get("Content-Type") or ""))
    tries = []
    if body[:2] in (b"\xff\xfe", b"\xfe\xff"):
        tries.append("utf-16")
    tries += ["utf-8-sig"] + ([m.group(1)] if m else []) + ["cp1252"]
    text, guessed = None, False
    for cs in tries:
        try:
            text = body.decode(cs)
            guessed = cs == "cp1252"            # the last resort decodes almost any bytes, so it proves nothing: the text must then look like plain text
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        text, guessed = body.decode("utf-8", "replace"), True
    bad = sum(1 for ch in text if ch == "\ufffd" or (ord(ch) < 32 and ch not in "\t\n\r") or 0x7f <= ord(ch) < 0xa0)
    if guessed:
        bad += sum(1 for ch in text if ord(ch) >= 0xa0) * 2     # a plain error text is mostly ASCII; mostly accented letters means the bytes were not text
    return text, bad * 10 <= max(len(text), 1)


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


_REQUEST_ID_HEADERS = ("X-Request-Id", "X-Request-ID", "X-Amzn-RequestId", "X-Correlation-Id")
_CODE_RE = re.compile(r"\"(?:code|errorCode)\"\s*:\s*\"([A-Za-z0-9_.-]{1,60})\"")


class Http(object):
    def __init__(self, creds, secrets=(), opener=None, sleep=time.sleep, timeout=60):
        self.creds, self.secrets, self.sleep, self.timeout = creds, set(secrets) | {creds.secret()}, sleep, timeout
        self.opener = opener or urllib.request.build_opener(_NoRedirect)
        self.calls = 0
        # Optional call recorder (the Emergency mode API log report): a callable given one dict per ATTEMPT {method, path, status, duration_ms, request_id, error_code,
        # error_text}. The path holds no query and the dict holds no header, token or request body. `record_text` False keeps the error CODE only (a record call's error text
        # could echo a value).
        self.recorder, self.record_text = None, True
        # `record_full` True adds the whole (cleaned, at most MAX_BODY characters) text of a refused reply to its recorder entry as `response_text`, for a report that must show
        # what Workfront said. Off by default, so every other report is unchanged.
        self.record_full = False

    def _record(self, method, path, status, t0, headers=None, request_id=None, error=None, raw=None):
        if self.recorder is None:
            return
        rid = request_id
        if not rid and headers is not None and hasattr(headers, "get"):
            rid = next((headers.get(h) for h in _REQUEST_ID_HEADERS if headers.get(h)), None)
        code = None
        if error:
            m = _CODE_RE.search(error)
            code = m.group(1) if m else None
        text = None
        if error:
            text = self.clean(error) if self.record_text else ("(text withheld: it may echo a record value)" if status else self.clean(error))
        entry = {"method": method, "path": path, "status": status, "duration_ms": int((time.monotonic() - t0) * 1000), "request_id": str(rid)[:80] if rid else None,
                 "error_code": code, "error_text": text}
        if self.record_full and raw is not None and status:
            entry["response_text"] = lc.redact(lr.scrub(read_text(raw, headers)[0], MAX_BODY), self.secrets)
        self.recorder(entry)

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
            req = urllib.request.Request(self.url(path, query), data=data, method=method, headers={"Content-Type": "application/json", "Accept": "application/json", "Accept-Encoding": "identity", name: value})
            self.calls += 1
            t0 = time.monotonic()
            try:
                with self.opener.open(req, timeout=self.timeout) as r:
                    raw = r.read()
                    status = r.status
                    heads = getattr(r, "headers", None)
            except urllib.error.HTTPError as e:
                status, raw = e.code, e.read()
                if 300 <= status < 400:
                    err = "The server answered with a redirect (%d), which is not followed so the credentials stay with the configured address." % status
                    self._record(method, path, status, t0, e.headers, error=err, raw=raw)
                    raise HttpError(err, status)
                if status not in RETRY_STATUS:
                    err = self._failure(status, raw, e.headers)
                    self._record(method, path, status, t0, e.headers, error=err, raw=raw)
                    raise HttpError(err, status)
                last = HttpError(self._failure(status, raw, e.headers), status)
                self._record(method, path, status, t0, e.headers, error=str(last), raw=raw)
            except (urllib.error.URLError, OSError) as e:
                last = HttpError(self.clean("Could not reach the server: %s" % getattr(e, "reason", e)), None)
                self._record(method, path, None, t0, error=str(last))
            else:
                if not raw.strip():
                    self._record(method, path, status, t0, heads)
                    return None
                try:
                    parsed = json.loads(read_text(raw, heads)[0])
                except ValueError:
                    self._record(method, path, status, t0, heads, error="The server's answer (HTTP %d) was not JSON." % status, raw=raw)
                    raise HttpError("The server's answer (HTTP %d) was not JSON." % status, status)
                self._record(method, path, status, t0, heads, request_id=(parsed.get("requestId") if isinstance(parsed, dict) else None))
                return parsed
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
                                             headers={"Content-Type": "multipart/form-data; boundary=" + boundary, "Content-Length": str(len(head) + size + len(tail)), "Accept": "application/json", "Accept-Encoding": "identity", name: value})
                self.calls += 1
                try:
                    with self.opener.open(req, timeout=self.timeout) as r:
                        raw, status, heads = r.read(), r.status, getattr(r, "headers", None)
                except urllib.error.HTTPError as e:
                    status, raw = e.code, e.read()
                    if 300 <= status < 400:
                        raise HttpError("The server answered with a redirect (%d), which is not followed so the credentials stay with the configured address." % status, status)
                    if status not in RETRY_STATUS:
                        raise HttpError(self._failure(status, raw, e.headers), status)
                    last = HttpError(self._failure(status, raw, e.headers), status)
                except (urllib.error.URLError, OSError) as e:
                    last = HttpError(self.clean("Could not reach the server: %s" % getattr(e, "reason", e)), None)
                else:
                    if not raw.strip():
                        return None
                    try:
                        return json.loads(read_text(raw, heads)[0])
                    except ValueError:
                        raise HttpError("The server's answer (HTTP %d) was not JSON." % status, status)
            if attempt < len(WAITS):
                self.sleep(WAITS[attempt])
        raise last

    def _failure(self, status, raw, headers=None):
        """HTTP status plus a short, cleaned excerpt of the error body (the error code is what a person needs). A body that cannot be read as text is described instead
        (size, Content-Type, Content-Encoding and its first bytes in hex, which show a compressed or binary reply), never shown as replacement characters."""
        text, ok = read_text(raw, headers)
        if not ok:
            get = (lambda k: headers.get(k)) if headers is not None and hasattr(headers, "get") else (lambda k: None)
            return self.clean("HTTP %d: the error text could not be read (%d bytes; Content-Type %s; Content-Encoding %s; starts with hex %s)."
                              % (status, len(raw or b""), get("Content-Type") or "none", get("Content-Encoding") or "none", (raw or b"")[:8].hex() or "none"))
        return self.clean("HTTP %d: %s" % (status, " ".join(text.split())))
