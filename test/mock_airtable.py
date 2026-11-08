#!/usr/bin/env python3
"""
Tiny stand-in for the Airtable API, for testing the control plane without touching real data.
Standard library only.

  python test/mock_airtable.py            (listens on 127.0.0.1:9100)

Then start the app against it:
  Mac/Linux:   AIRTABLE_API_BASE=http://127.0.0.1:9100 AT_BACKOFF_BASE=0.2 python server.py
  Windows cmd: set AIRTABLE_API_BASE=http://127.0.0.1:9100 && set AT_BACKOFF_BASE=0.2 && python server.py

Behaviour:
  * GET /v0/meta/bases/<base>/tables  serves test/sample_schema.json (base appSampleBase001)
    or test/sample_schema_errors.json (base appSampleBase002)
  * GET /v0/<base>/<table>            serves generated records, 100 per page, with offset tokens
  * table tblFlaky0000001             answers 429 (with Retry-After: 1) twice, then succeeds
  * table tblForbidden0001            always answers 403
  * any token other than the one in MOCK_TOKEN (default "patMOCK.0000000000000000000000") gets 401
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN = os.environ.get("MOCK_TOKEN", "patMOCK.0000000000000000000000")
SCHEMAS = {"appSampleBase001": "sample_schema.json", "appSampleBase002": "sample_schema_errors.json"}
ROWS = {"tblCustomers0001": 250, "tblOrders000001": 120, "tblProducts0001": 30, "tblOrderItems001": 301,
        "tblDepartments01": 5, "tblEmployees0001": 40, "tblArticles0001": 12, "tblTags00000001": 8,
        "tblFlaky0000001": 150, "tblWideCard00001": 1000}
flaky_hits = {"n": 0}


def load(base):
    with open(os.path.join(HERE, SCHEMAS[base]), encoding="utf-8") as f:
        return json.load(f)


def make_record(table, i):
    """Deterministic fake record. Empty values are omitted, like Airtable does."""
    fields = {}
    for f in table["fields"]:
        fid, t = f["id"], f["type"]
        if t == "checkbox":
            if i % 3 == 0:
                fields[fid] = True            # unchecked boxes are omitted by Airtable
        elif t == "multipleAttachments":
            if i % 4 == 0:
                fields[fid] = [{"id": "att%d" % (i % 7), "url": "https://example.invalid/x", "filename": "f.png"}]
        elif t == "multipleRecordLinks":
            if i % 2 == 0:
                fields[fid] = ["rec%05d" % (i % 10)]
            elif i % 5 == 1:
                fields[fid] = []              # an empty array, counted as null
        elif t in ("number", "currency", "autoNumber"):
            fields[fid] = 0 if i % 5 == 0 else i % 17   # zero is a real value
        elif f["name"] == "Unique Key":
            fields[fid] = "key-%d" % i                  # every value distinct
        elif f["name"] == "Bucket":
            fields[fid] = "b%d" % (i % 4)
        elif t == "email":
            if i % 10 != 0:
                fields[fid] = "user%d@example.com" % (i % 50)
        else:
            if i % 6 == 1:
                fields[fid] = ""              # empty string, counted as null
            elif i % 6 != 2:
                fields[fid] = "%s %d" % (f["name"], i % 20)
    return {"id": "rec%05d" % i, "createdTime": "2026-01-01T00:00:00.000Z", "fields": fields}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, status, obj, headers=None):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer " + TOKEN:
            return self._json(401, {"error": {"type": "AUTHENTICATION_REQUIRED", "message": "Authentication required"}})
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        q = parse_qs(u.query)
        if len(parts) == 5 and parts[:3] == ["v0", "meta", "bases"] and parts[4] == "tables":
            if parts[3] not in SCHEMAS:
                return self._json(404, {"error": {"type": "NOT_FOUND", "message": "Could not find base"}})
            doc = load(parts[3])
            if q.get("include") != ["visibleFieldIds"]:   # like the real API: visible fields only when asked for
                doc = json.loads(json.dumps(doc))
                for t in doc["tables"]:
                    for v in t.get("views", []):
                        v.pop("visibleFieldIds", None)
            return self._json(200, doc)
        if len(parts) == 3 and parts[0] == "v0" and parts[1] in SCHEMAS:
            base, tid = parts[1], parts[2]
            table = next((t for t in load(base)["tables"] if t["id"] == tid), None)
            if not table:
                return self._json(404, {"error": {"type": "TABLE_NOT_FOUND", "message": "Table not found"}})
            if tid == "tblForbidden0001":
                return self._json(403, {"error": {"type": "INVALID_PERMISSIONS_OR_MODEL_NOT_FOUND", "message": "Invalid permissions"}})
            if tid == "tblFlaky0000001" and flaky_hits["n"] < 2:
                flaky_hits["n"] += 1
                return self._json(429, {"error": {"type": "RATE_LIMIT_REACHED", "message": "Too many requests"}}, {"Retry-After": "1"})
            if q.get("returnFieldsByFieldId") != ["true"]:
                return self._json(422, {"error": {"type": "INVALID_REQUEST_UNKNOWN", "message": "returnFieldsByFieldId must be true in this mock"}})
            total = ROWS.get(tid, 10)
            start = int(q.get("offset", ["0"])[0].replace("itr", "") or 0)
            size = int(q.get("pageSize", ["100"])[0])
            end = min(start + size, total)
            out = {"records": [make_record(table, i) for i in range(start, end)]}
            if end < total:
                out["offset"] = "itr%d" % end
            return self._json(200, out)
        return self._json(404, {"error": "NOT_FOUND"})


if __name__ == "__main__":
    port = int(os.environ.get("MOCK_PORT", "9100"))
    print("Mock Airtable on http://127.0.0.1:%d (token: %s)" % (port, TOKEN))
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
