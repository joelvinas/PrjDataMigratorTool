"""Tests for the re-key step (rekey.py): making the Airtable primary field the primary field of each record type in the DEV Workspace, after the data is validated. Fabricated names, a fake
token, a FAKE Planning, no network. Covers: which record types are eligible (a real text field only), the ones left alone with their reason, that the check only reads, the person's decision
(initials, reason, typed phrase), the switch itself with the record type's name sent and verified, a failing record type that never stops the others, a name that drifts being reported,
and that the report holds who, when and why and no secret."""
import io, json, os, sqlite3, sys, tempfile, urllib.error
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
import rekey as rk, tableload as tl
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:600])

TOKEN = "fakePLANtoken0123456789"
BASE = "appRekey0001"
tmp = tempfile.mkdtemp(prefix="rekey_")
dbp = os.path.join(tmp, "base.db")


class Resp(object):
    def __init__(self, status, body):
        self.status, self._b, self.headers = status, json.dumps(body).encode(), {}
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False


class Fake(object):
    def __init__(self):
        self.reqs, self.types, self.primary, self.fields, self.refuse, self.rename_on_patch = [], {}, {}, {}, set(), set()
    def add(self, rt, name, fields, primary):
        self.types[rt], self.fields[rt], self.primary[rt] = name, fields, primary
    def open(self, req, timeout=None):
        body = json.loads(req.data) if req.data else None
        m, full = req.get_method(), req.full_url
        self.reqs.append((m, full, body))
        u = full.split("?")[0]
        if m == "GET" and u.endswith("/fields"):
            rt = u.split("/record-types/")[1].split("/")[0]
            return Resp(200, {"content": self.fields[rt], "cursor": {"hasMore": False}})
        if m == "GET" and "/record-types/" in u:
            rt = u.split("/record-types/")[1]
            return Resp(200, {"id": rt, "displayName": self.types[rt], "primaryFieldId": self.primary[rt]})
        if m == "PATCH" and "/record-types/" in u:
            rt = u.split("/record-types/")[1]
            if rt in self.refuse:
                raise urllib.error.HTTPError(full, 400, "bad", {}, io.BytesIO(json.dumps({"code": "PRIMARY_REFUSED", "message": "no"}).encode()))
            self.primary[rt] = body["primaryFieldId"]
            self.types[rt] = body["displayName"] + (" (renamed)" if rt in self.rename_on_patch else "")
            return Resp(200, {"id": rt})
        return Resp(404, {})
    def patches(self): return [x for x in self.reqs if x[0] == "PATCH"]


def _rejects(conn):
    try:
        conn.execute("insert into rekey_decisions(run_id, decided_at, decided_by, reason, base_id, environment, workspace_key, table_id, outcome) values ('x','t','a','b','c','d','e','f','bogus')")
        return False
    except sqlite3.IntegrityError:
        return True


c = sqlite3.connect(dbp); c.row_factory = sqlite3.Row
c.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
TABLES = (("tOrd", "Orders", "fOName"), ("tCus", "Customers", "fCName"), ("tPre", "Parts", "fPName"), ("tGone", "Gone", "fGName"), ("tAlr", "Already", "fAName"), ("tNop", "No Primary", None), ("tBad", "Bad Ones", "fBName"),
          ("tDrift", "Drifters", "fDName"))
for tid, nm, pf in TABLES:
    c.execute("insert into airtable_tables(table_id, base_id, table_name, primary_field_id) values (?,?,?,?)", (tid, BASE, nm, pf))
    if pf:
        c.execute("insert into airtable_fields(base_id, table_id, table_name, field_id, field_name, data_type, updated_at) values (?,?,?,?,?,'singleLineText','2026-01-01 10:00:00.000000')", (BASE, tid, nm, pf, nm + " Name"))
def lm(temp, kind, tid, fid, name, wid):
    c.execute("insert into load_id_map(temp_id, environment, workspace_id, workspace_key, system, kind, base_id, table_id, airtable_field_id, name, workfront_id, status) values (?,?,?,?,?,?,?,?,?,?,?, 'created')",
              (temp, "dev", "wsDev", "wsDev", "planning", kind, BASE, tid, fid, name, wid))
fk = Fake()
for i, (tid, nm, pf) in enumerate(TABLES):
    rt = "RT%d" % i
    lm("P-T-" + tid, "record_type", tid, None, nm, rt)
    arid = {"id": "ARID%d" % i, "displayName": "AirtableRecordId", "type": "text"}
    fields = [arid]
    typ = {"tCus": "long-text"}.get(tid, "text")
    pname = {"tPre": "PRE-" + nm + " Name"}.get(tid, nm + " Name")
    if pf and tid != "tGone":
        fields.append({"id": "FP%d" % i, "displayName": pname, "type": typ})
        lm("P-F-" + pf, "field", tid, pf, pname, "FP%d" % i)
    fk.add(rt, nm, fields, "FP%d" % i if tid == "tAlr" else "ARID%d" % i)
c.commit()
cfgp = os.path.join(tmp, "config.json")
json.dump({"token": "patFAKE.0", "workfront": {"planning": {"dev": {"base_url": "https://acme.my.workfront.com", "bearer_token": TOKEN, "workspace_id": "wsDev"}}}}, open(cfgp, "w"))
ctx = tl.planning_context(cfgp, "dev", True)
tr = tl.transport(ctx, opener=fk)

rows = rk.candidates(c, BASE, "dev", "wsDev")
st = {r["table_id"]: r["status"] for r in rows}
check("the record types of the stage come back sorted by table name", [r["table_name"] for r in rows] == sorted([r["table_name"] for r in rows], key=str.lower) and len(rows) == 8)
check("a table with no Airtable primary field, and one whose primary field is not in Planning, are known without reading Planning", st["tNop"] == "no_primary" and st["tGone"] == "no_field" and st["tOrd"] == "unchecked"
      and all(len(fk.reqs) == 0 for _ in [0]), st)
check("the Airtable and Planning field names are carried", [(r["airtable_field_name"], r["planning_field_name"]) for r in rows if r["table_id"] == "tOrd"] == [("Orders Name", "Orders Name")])
rk.check(rows, tr)
st = {r["table_id"]: r["status"] for r in rows}
check("a real text field is eligible, one that is already primary says so", st["tOrd"] == "eligible" and st["tAlr"] == "already" and st["tBad"] == "eligible" and st["tDrift"] == "eligible", st)
by = {r["table_id"]: r for r in rows}
check("a long-text field and a PRE- field are left alone with their reason", st["tCus"] == "ineligible" and "long-text" in by["tCus"]["reason"] and "only a text field" in by["tCus"]["reason"]
      and st["tPre"] == "ineligible" and "PRE-" in by["tPre"]["reason"], by["tCus"])
check("the check only reads (no PATCH was sent) and records the current primary field's name", not fk.patches() and by["tOrd"]["current_primary_name"] == "AirtableRecordId")
check("the counts add up", rk.counts(rows)["eligible"] == 3 and rk.counts(rows)["already"] == 1 and rk.counts(rows)["ineligible"] == 2 and rk.counts(rows)["no_field"] == 1 and rk.counts(rows)["no_primary"] == 1, rk.counts(rows))

# ---- the person's decision
for args, why in ((("", "validated", rk.PHRASE), "initials"), (("JV", "", rk.PHRASE), "a reason"), (("JV", "validated", "rekey"), "the phrase"), (("J" * 41, "validated", rk.PHRASE), "short initials")):
    try:
        rk.validate_decision(*args); check("a decision without %s is refused" % why, False)
    except rk.RekeyError as e:
        check("a decision without %s is refused" % why, bool(e.message))
check("a decision with initials, a reason and the exact phrase is accepted", rk.validate_decision(" JV ", " validated 23 Nov ", " " + rk.PHRASE + " ") == ("JV", "validated 23 Nov"))

# ---- the switch
fk.refuse = {"RT6"}                          # tBad: Planning refuses
fk.rename_on_patch = {"RT7"}                 # tDrift: the name changes after the switch
res = rk.run(rows, tr, ["tOrd", "tBad", "tDrift", "tCus", "tAlr", "tNOPE"])
out = {r["table_id"]: r for r in res}
check("results come back in table name order and the unknown table is skipped", [r["table_name"] for r in res][:5] == ["Already", "Bad Ones", "Customers", "Drifters", "Orders"] and out["tNOPE"]["outcome"] == "skipped", res)
check("an eligible record type is re-keyed to its Airtable primary field, sending the record type's own name", out["tOrd"]["outcome"] == "rekeyed" and fk.primary["RT0"] == "FP0" and fk.types["RT0"] == "Orders"
      and [x[2] for x in fk.patches() if x[1].endswith("/RT0")] == [{"displayName": "Orders", "primaryFieldId": "FP0"}], fk.patches())
check("AirtableRecordId is not deleted or renamed: only record types are patched", all("/record-types/" in x[1] for x in fk.patches()))
check("a refused switch fails that record type only, with the error code, and the others went on", out["tBad"]["outcome"] == "failed" and "PRIMARY_REFUSED" in out["tBad"]["detail"] and out["tOrd"]["outcome"] == "rekeyed")
check("a record type whose name changes during the switch is reported, not hidden", out["tDrift"]["outcome"] == "failed" and "no longer" in out["tDrift"]["detail"], out["tDrift"])
check("an ineligible or already-done record type is skipped with its reason and not patched", out["tCus"]["outcome"] == "skipped" and "only a text field" in out["tCus"]["detail"] and out["tAlr"]["outcome"] == "skipped"
      and not [x for x in fk.patches() if x[1].endswith("/RT1") or x[1].endswith("/RT4")])
res2 = rk.run(rows, tr, ["tOrd"])
check("running again does not switch a done record type a second time", res2[0]["outcome"] == "skipped" and len([x for x in fk.patches() if x[1].endswith("/RT0")]) == 1, res2)

# ---- the decision in the database (draft migration 029): skipped while the table is missing, appended once it exists, never changed afterwards
info0 = {"base_id": BASE, "workspace_id": "wsDev", "stage": "dev", "actor": "JV", "reason": "data validated 23 November", "started_at": "2026-11-23 20:00:00.123456", "finished_at": "2026-11-23 20:01:00", "rows": rows, "results": res}
w0, note0 = rk.store_decisions(c, info0, "dev")
check("with no rekey_decisions table the write is skipped and says so", w0 is False and "migration 029" in note0 and "report only" in note0, note0)
c.executescript(open(os.path.join(ROOT, "sql", "migration_029_add_rekey_decisions.sql"), encoding="utf-8").read())
c.executescript(open(os.path.join(ROOT, "sql", "migration_029_add_rekey_decisions.sql"), encoding="utf-8").read())      # repeatable
w1, note1 = rk.store_decisions(c, info0, "dev")
dr = {r["table_id"]: dict(r) for r in c.execute("select * from rekey_decisions")}
check("the migration is repeatable and one row per record type of the run is stored, with who, when and why", w1 and len(dr) == len(res) and rk.run_id_of(info0) == "R20261123200000-appRekey0001"
      and all(r["decided_by"] == "JV" and r["reason"] == "data validated 23 November" and r["decided_at"] == "2026-11-23 20:00:00.123456" and r["environment"] == "dev" and r["workspace_key"] == "wsDev" for r in dr.values()), (note1, list(dr)[:2]))
check("each row carries the IDs and the outcome", dr["tOrd"]["outcome"] == "rekeyed" and dr["tOrd"]["planning_field_id"] == "FP0" and dr["tOrd"]["airtable_field_id"] == "fOName" and dr["tOrd"]["previous_primary_field_id"] == "ARID0"
      and dr["tBad"]["outcome"] == "failed" and "PRIMARY_REFUSED" in dr["tBad"]["detail"] and dr["tCus"]["outcome"] == "skipped", dr["tOrd"])
w2, _n = rk.store_decisions(c, info0, "dev")
check("storing the same run again changes nothing (a run is only ever added)", c.execute("select count(*) from rekey_decisions").fetchone()[0] == len(res))
check("the outcome is limited to rekeyed, skipped or failed", _rejects(c))

# ---- the report
info = {"base_id": BASE, "base_name": "Fabricated Base", "workspace_id": "wsDev", "stage": "dev", "actor": "JV", "reason": "data validated 23 November", "started_at": "2026-11-23T20:00:00Z", "finished_at": "2026-11-23T20:01:00Z",
        "rows": rows, "results": res}
name = rk.write_report(info, os.path.join(tmp, "out"), lambda html, title: html.encode("utf-8"), "")
text = open(os.path.join(tmp, "out", name), encoding="utf-8").read()
check("the report says who, when and why, and lists every record type with its outcome", name == "Rekey_appRekey0001_20261123-2000.mhtml" and "JV" in text and "data validated 23 November" in text and "2026-11-23T20:00:00Z" in text
      and "Orders" in text and "rekeyed" in text and "PRIMARY_REFUSED" in text and 'tabindex="0"' in text)
check("no token is in the report", TOKEN not in text)
print("DONE" if ok else "SOME CHECKS FAILED")
sys.exit(0 if ok else 1)
