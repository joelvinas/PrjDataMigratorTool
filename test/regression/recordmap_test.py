"""Tests for recordmap.py (the record ID map, migration 017). Fabricated IDs only; in-memory database, no server."""
import hashlib, os, random, sqlite3, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import recordmap as rm
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:300])

def db():
    c = sqlite3.connect(":memory:"); c.row_factory = sqlite3.Row; c.execute("PRAGMA foreign_keys = ON")
    c.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
    for tid, nm in (("tA", "Orders"), ("tB", "Customers")):
        c.execute("INSERT INTO airtable_tables(table_id, base_id, table_name) VALUES (?,?,?)", (tid, "appL", nm))
    c.commit()
    return c
def err(fn, *a, **k):
    try:
        fn(*a, **k); return None
    except rm.RecordMapError as e:
        return e.message

c = db()
IDS = ["recC", "recA", "recB", "recA", "bad id!", "", None, "recD"]
r = rm.register(c, "appL", "tA", IDS)
check("records register in sorted order, once each; invalid IDs are counted and skipped", r == {"total": 4, "new": 4, "already": 0, "skipped_invalid": 3} and
      list(rm.uid_map(c, "appL", "tA").items()) == [("recA", 1), ("recB", 2), ("recC", 3), ("recD", 4)], (r, rm.uid_map(c, "appL", "tA")))
r2 = rm.register(c, "appL", "tA", ["recA", "recE", "recB"])
check("registering again keeps every surrogate ID and adds only the new record", r2["new"] == 1 and r2["already"] == 2 and rm.lookup(c, "appL", "tA", "recA") == 1 and rm.lookup(c, "appL", "tA", "recE") == 5)
a, b = db(), db()
ids = ["rec%03d" % i for i in range(50)]
shuffled = ids[:]; random.Random(7).shuffle(shuffled)
rm.register(a, "appL", "tA", ids); rm.register(b, "appL", "tA", shuffled)
check("the same records in any input order give the same surrogate IDs (no randomness)", rm.uid_map(a, "appL", "tA") == rm.uid_map(b, "appL", "tA"))
check("the same record ID in another table is a different record", rm.register(c, "appL", "tB", ["recA"])["new"] == 1 and rm.lookup(c, "appL", "tB", "recA") != rm.lookup(c, "appL", "tA", "recA"))
check("a table that is not in the database is refused", "not in the database" in (err(rm.register, c, "appL", "tNope", ["recA"]) or "") and "not in the database" in (err(rm.register, c, "appOther", "tA", ["recA"]) or ""))
check("the same base, table and record ID can never be stored twice", c.execute("SELECT COUNT(*) FROM (SELECT 1 FROM source_records GROUP BY base_id, table_id, record_id HAVING COUNT(*) > 1)").fetchone()[0] == 0)
check("a database without migration 017 says what is missing", "migration 017" in (err(rm.register, sqlite3.connect(":memory:"), "a", "b", []) or "") and not rm.available(sqlite3.connect(":memory:")))

# ---- targets: fan-out and consolidation
uA, uB, uC = (rm.lookup(c, "appL", "tA", x) for x in ("recA", "recB", "recC"))
check("a target mapping is added once, and the same mapping again is not a new row", rm.add_target(c, uA, "core", "sandbox1", "", "project", "PRJ1") is True and rm.add_target(c, uA, "core", "sandbox1", "", "project", "PRJ1") is False)
rm.add_target(c, uA, "planning", "dev", "wsDev", "record", "PL1"); rm.add_target(c, uA, "core", "sandbox1", "", "task", "TSK9")
check("one source record can have several targets (fan-out), listed in order", rm.targets_of(c, uA, "core", "sandbox1") == [("project", "PRJ1"), ("task", "TSK9")] and rm.targets_of(c, uA, "planning", "dev", "wsDev") == [("record", "PL1")])
rm.add_target(c, uB, "core", "sandbox1", "", "project", "PRJ1")
check("several source records can become one target (consolidation), found by the target", rm.sources_of(c, "core", "sandbox1", "PRJ1") == [("appL", "tA", "recA"), ("appL", "tA", "recB")])
check("a mapping is per environment: the same target ID in production is a different mapping", rm.targets_of(c, uA, "core", "production") == [])
check("a Planning mapping needs its Workspace; a Core mapping has none", "Workspace" in (err(rm.add_target, c, uA, "planning", "dev", "", "record", "X") or "") and "no Workspace" in (err(rm.add_target, c, uA, "core", "sandbox1", "wsDev", "project", "X") or ""))
check("an unknown record, environment or empty target is refused", err(rm.add_target, c, 99999, "core", "sandbox1", "", "project", "X") is not None and err(rm.add_target, c, uA, "core", "staging", "", "project", "X") is not None and err(rm.add_target, c, uA, "core", "sandbox1", "", "project", " ") is not None)

# ---- pass markers
check("a link pass cannot be marked for a record whose records pass is not done", "records pass" in (err(rm.mark_pass, c, [uA], "links", "sandbox1", "core") or ""))
check("the records pass is marked once per record", rm.mark_pass(c, [uA, uB], "records", "sandbox1", "core") == 2 and rm.mark_pass(c, [uA, uB], "records", "sandbox1", "core") == 0)
check("after the records pass the links pass can be marked", rm.mark_pass(c, [uA], "links", "sandbox1", "core") == 1)
check("a restart sees only the records still to do, in order", [x[1] for x in rm.pending_records(c, "appL", "tA", "records", "sandbox1", "core")] == ["recC", "recD", "recE"] and [x[1] for x in rm.pending_records(c, "appL", "tA", "links", "sandbox1", "core")] == ["recB", "recC", "recD", "recE"])
check("the markers are per environment", len(rm.pending_records(c, "appL", "tA", "records", "production", "core")) == 5)
check("an unknown pass is refused", err(rm.mark_pass, c, [uA], "teleport", "sandbox1", "core") is not None)
big = db(); rm.register(big, "appL", "tA", ["r%05d" % i for i in range(1500)])
all_uids = sorted(rm.uid_map(big, "appL", "tA").values())
check("a large batch (more records than one SQL statement takes) is marked in chunks", rm.mark_pass(big, all_uids, "records", "dev", "planning", "wsX") == 1500 and rm.mark_pass(big, all_uids, "links", "dev", "planning", "wsX") == 1500)

# ---- attachments
sha = hashlib.sha256(b"fabricated bytes").hexdigest()
row = rm.plan_attachment(c, uA, "fldAtt1", "attOne", "sandbox1", 16)
check("planning an attachment twice gives the same row, so a rerun never makes a second", rm.plan_attachment(c, uA, "fldAtt1", "attOne", "sandbox1", 16) == row and c.execute("SELECT COUNT(*) FROM attachment_map").fetchone()[0] == 1)
check("a new attachment starts pending", rm.attachments_with_status(c, "sandbox1", "pending")[0]["attachment_row_id"] == row)
check("an upload before a checked download is refused", "downloaded" in (err(rm.attachment_uploaded, c, row, "DOC1") or ""))
check("a download whose size matches what Airtable reported is accepted", rm.attachment_downloaded(c, row, 16, sha) is True and rm.attachments_with_status(c, "sandbox1", "downloaded")[0]["sha256"] == sha)
check("a malformed hash or size is refused", err(rm.attachment_downloaded, c, row, 16, "xyz") is not None and err(rm.attachment_downloaded, c, row, -1, sha) is not None)
check("the upload is recorded once with the Core document ID", rm.attachment_uploaded(c, row, "DOC1") is True and rm.attachment_uploaded(c, row, "DOC1") is False)
check("a file is never uploaded twice as a different document", "never uploaded twice" in (err(rm.attachment_uploaded, c, row, "DOC2") or ""))
check("an uploaded file is never downloaded again", "already uploaded" in (err(rm.attachment_downloaded, c, row, 16, sha) or ""))
row2 = rm.plan_attachment(c, uA, "fldAtt1", "attTwo", "sandbox1", 100)
check("a size that differs from what Airtable reported fails the row, with a plain message", rm.attachment_downloaded(c, row2, 90, sha) is False and
      c.execute("SELECT status, last_error FROM attachment_map WHERE attachment_row_id=?", (row2,)).fetchone()[0] == "failed" and "differs" in c.execute("SELECT last_error FROM attachment_map WHERE attachment_row_id=?", (row2,)).fetchone()[0])
row3 = rm.plan_attachment(c, uB, "fldAtt1", "attThree", "sandbox1")
rm.attachment_failed(c, row3, "HTTP 403 Authorization: Bearer abc.def.ghi token=SECRETVALUE")
le = c.execute("SELECT status, last_error FROM attachment_map WHERE attachment_row_id=?", (row3,)).fetchone()
check("a failure message is stored scrubbed of credentials", le[0] == "failed" and "SECRETVALUE" not in le[1] and "[removed]" in le[1], le[1])
rm.attachment_failed(c, row, "late error")
check("a failure never undoes an upload", c.execute("SELECT status FROM attachment_map WHERE attachment_row_id=?", (row,)).fetchone()[0] == "uploaded")
check("the attachment table has no column that could hold a file name or link", not ({x[1] for x in c.execute("PRAGMA table_info(attachment_map)")} & {"file_name", "filename", "url", "link", "name"}))

# ---- the summary: counts only
s = rm.summary(c, "appL", "sandbox1", "core")
ta = {t["table_name"]: t for t in s["tables"]}
check("the summary gives counts only: registered, passes, targets, attachment statuses", s["registered"] == 6 and ta["Orders"]["registered"] == 5 and ta["Orders"]["passes"] == {"records": 2, "links": 1, "attachments": 0, "core_backfill": 0}
      and ta["Orders"]["targets"] == 3 and s["attachments"] == {"pending": 0, "downloaded": 0, "uploaded": 1, "failed": 2}, s)
check("without an environment the summary lists only the registered counts", "passes" not in rm.summary(c, "appL")["tables"][0] and "attachments" not in rm.summary(c, "appL"))

print("ALL PASS" if ok else "FAILURES")
sys.exit(0 if ok else 1)
