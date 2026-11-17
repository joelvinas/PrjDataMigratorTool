"""Tests for attachments.py and `loader.py attachments`: Stage 1's downloaded files become Core documents on the Core objects their records became. A fake opener stands in for
Workfront; fabricated records, files and fake secrets only. (Named attachments_move_test so it is not confused with attach_test, which tests Stage 1's downloads.)"""
import hashlib, io, json, os, shutil, sqlite3, sys, tempfile, urllib.error
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
import attachments as at, coretargets as tg, loader, recordmap as rm
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:400])
def refused(argv, **k):
    try:
        loader.main(argv, **k); return None
    except SystemExit as e:
        return str(e.code)

KEY = "fakeCOREkey0123456789"
GUID = "0123456789abcdef0123456789abcdef"
SECRET_NAME = "invoice-SECRETNAME.pdf"
tmp = tempfile.mkdtemp(prefix="attmove_")
dbp = os.path.join(tmp, "base.db")
c = sqlite3.connect(dbp); c.row_factory = sqlite3.Row; c.execute("PRAGMA foreign_keys = ON")
c.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
c.execute("INSERT INTO airtable_tables(table_id, base_id, table_name, primary_field_id) VALUES ('tReq','appL','Requests','fldTitle')")
c.execute("INSERT INTO table_targets(table_id, base_id, target, decided_by) VALUES ('tReq','appL','planning_and_core','JV')")
c.commit()
tg.set_object_type(c, "appL", "tReq", "request", "JV"); tg.set_parent(c, "appL", "tReq", "PROJ", GUID, "JV", "queue")
c.close()

ex = os.path.join(tmp, "extract", "appL"); os.makedirs(os.path.join(ex, "attachments"))
FILES = {"attA1": b"alpha file bytes", "attA2": b"beta file bytes!!", "attB1": b"gamma"}
def put(aid, data):
    open(os.path.join(ex, "attachments", aid + ".pdf"), "wb").write(data)
for k, v in FILES.items():
    put(k, v)
sha = lambda b: hashlib.sha256(b).hexdigest()
def entry(rec, field, aid, status="downloaded", size=None, data=None, name=SECRET_NAME, err=None):
    d = FILES.get(aid) if data is None else data
    return {"record_id": rec, "field_id": field, "attachment_id": aid, "filename": name, "type": "application/pdf", "size": len(d) if (d is not None and size is None) else size,
            "file": ("attachments/%s.pdf" % aid) if status != "failed" else None, "bytes": len(d) if d is not None and status != "failed" else None, "sha256": sha(d) if d is not None and status != "failed" else None,
            "status": status, "error": err}
json.dump({"format": "airtable-table-extract/1", "base_id": "appL", "table_id": "tReq", "table_name": "Requests",
           "records": [{"id": "recR1", "fields": {"fldTitle": "One"}}, {"id": "recR2", "fields": {"fldTitle": "Two"}}, {"id": "recR3", "fields": {"fldTitle": "Three"}}]}, open(os.path.join(ex, "tReq.json"), "w"))
json.dump({"format": "airtable-attachment-index/1", "base_id": "appL", "table_id": "tReq", "attachments": [
    entry("recR1", "fldFile", "attA1"), entry("recR1", "fldFile", "attA2"),
    entry("recR2", "fldFile", "attB1", size=999),                                            # Airtable said 999 bytes, the file has 5: the size check fails it
    entry("recR3", "fldFile", "attC1", status="failed", data=b"", err="link expired or file no longer available (HTTP 410)"),
    entry("recZ", "fldFile", "attZ1", data=b"zz")]}, open(os.path.join(ex, "tReq.attachments.json"), "w"))
cfgp = os.path.join(tmp, "config.json")
json.dump({"workfront": {"core": {"sandbox1": {"base_url": "https://acme.sb01.workfront.com", "api_path": "/attask/api/v21.0", "api_key": KEY},
                                  "production": {"base_url": "https://acme.my.workfront.com", "api_path": "/attask/api/v21.0", "api_key": KEY + "P"}}}}, open(cfgp, "w"))

class Resp(object):
    def __init__(self, status, body): self.status, self._b = status, body if isinstance(body, bytes) else json.dumps(body).encode()
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False
class Fake(object):
    def __init__(self, fail_upload_once=False):
        self.reqs, self.n, self.fail_once = [], 0, fail_upload_once
    def open(self, req, timeout=None):
        raw = req.data.read() if hasattr(req.data, "read") else req.data
        path = req.full_url.split("workfront.com", 1)[1].split("/v21.0", 1)[1]
        self.reqs.append((req.get_method(), path, dict(req.header_items()), raw))
        self.n += 1
        if path == "/upload":
            if self.fail_once:
                self.fail_once = False
                raise urllib.error.HTTPError(req.full_url, 400, "x", {}, io.BytesIO(("{\"error\":\"bad Authorization: Bearer abc.def.ghi apiKey=%s\"}" % KEY).encode()))
            return Resp(200, {"data": {"handle": "H%03d" % self.n}})
        if path == "/docu":
            return Resp(200, {"data": {"ID": "DOC%029d" % self.n}})
        return Resp(200, {"data": {"ID": "%032x" % self.n}})
    def of(self, p): return [r for r in self.reqs if r[1] == p]
out = lambda name: os.path.join(tmp, "o_" + name)
def objects(db, fk):
    loader.main(["core-objects", "--db", db, "--base", "appL", "--config", cfgp, "--extract-dir", os.path.join(tmp, "extract"), "--out", out("obj"), "--send"], opener=fk)
def atts(db, fk, *x, name="att"):
    return loader.main(["attachments", "--db", db, "--base", "appL", "--config", cfgp, "--extract-dir", os.path.join(tmp, "extract"), "--out", out(name)] + list(x), opener=fk)

# ---- the module: plan reads the index and checks sizes
c = sqlite3.connect(dbp); c.row_factory = sqlite3.Row
rm.register(c, "appL", "tReq", ["recR1", "recR2", "recR3"])
r = at.plan_table(c, "appL", "tReq", "sandbox1", os.path.join(tmp, "extract"))
check("the plan reads the index: two good files downloaded, one size mismatch and one Stage 1 failure failed, an unregistered record counted", (r["indexed"], r["planned"], r["downloaded"], r["failed"], r["unregistered"]) == (5, 4, 2, 2, 1), r)
check("a size that differs from what Airtable reported fails the row with a plain reason", c.execute("select status, last_error from attachment_map where airtable_attachment_id='attB1'").fetchone()[1].startswith("the downloaded size"))
check("a Stage 1 failure keeps its short reason and has no file to send", "HTTP 410" in c.execute("select last_error from attachment_map where airtable_attachment_id='attC1'").fetchone()[0])
check("planning again changes nothing", at.plan_table(c, "appL", "tReq", "sandbox1", os.path.join(tmp, "extract"))["planned"] == 4 and c.execute("select count(*) from attachment_map").fetchone()[0] == 4)
check("a path in the index that points outside the base's folder is refused", at.local_path(os.path.join(tmp, "extract"), "appL", {"file": "../../config.json"}) is None and at.local_path(os.path.join(tmp, "extract"), "appL", {"file": "attachments/attA1.pdf"}) is not None)
c.close()
os.remove(dbp) if False else None

# ---- the command
db1 = os.path.join(tmp, "one.db"); shutil.copy(dbp, db1)
c = sqlite3.connect(db1); c.execute("delete from attachment_map"); c.execute("delete from source_records"); c.commit(); c.close()
fk = Fake()
atts(db1, fk)
check("without --send nothing is sent and nothing is written", fk.reqs == [] and sqlite3.connect(db1).execute("select count(*) from attachment_map").fetchone()[0] == 0
      and "nothing was sent" in open(os.path.join(out("att"), "attachments_summary.txt"), encoding="utf-8").read())
fk = Fake()
objects(db1, fk)                                                                        # registers the records and creates their Core objects
n_obj = len(fk.reqs)
fk = Fake()
res = atts(db1, fk, "--send", name="att1")["result"]
ups = fk.of("/upload"); docs = fk.of("/docu")
check("only the two good files are uploaded, as multipart with the file bytes, then made documents", len(ups) == 2 and len(docs) == 2 and all(b"filename=" in u[3] and u[2].get("Content-type", "").startswith("multipart/form-data") for u in ups)
      and sorted(u[3].split(b"\r\n\r\n", 1)[1].split(b"\r\n--")[0] for u in ups) == sorted([FILES["attA1"], FILES["attA2"]]), [(u[1], u[3][:60]) for u in ups])
objs = {}
c = sqlite3.connect(db1)
uid1 = rm.lookup(c, "appL", "tReq", "recR1")
obj_id = rm.targets_of(c, uid1, "core", "sandbox1")[0][1]
bodies = [json.loads(d[3]) for d in docs]
check("each document is created on the record's Core object (an OPTASK) with the uploaded handle", all(b["docObjCode"] == "OPTASK" and b["objID"] == obj_id and b["handle"].startswith("H") and b["currentVersion"]["fileName"] == SECRET_NAME for b in bodies), bodies)
rows = {r[0]: r[1:] for r in c.execute("select airtable_attachment_id, status, core_document_id from attachment_map")}
check("the map records uploaded documents, the size failure and the Stage 1 failure", rows["attA1"][0] == "uploaded" and rows["attA1"][1].startswith("DOC") and rows["attB1"][0] == "failed" and rows["attC1"][0] == "failed", rows)
check("only the record whose attachments are all done gets the attachments marker", [r[0] for r in c.execute("select record_id from record_pass_markers m join source_records s using(source_record_uid) where pass='attachments'")] == ["recR1"])
check("no file name or link is in the database or the reports", SECRET_NAME.encode() not in open(db1, "rb").read() and all(SECRET_NAME not in open(os.path.join(out("att1"), f), encoding="utf-8-sig").read() for f in os.listdir(out("att1"))))
c.close()
fk = Fake()
atts(db1, fk, "--send", name="att2")
check("a rerun uploads nothing: an attachment is never uploaded twice", fk.reqs == [])

# ---- a changed file is not uploaded; a failed upload is retried next run; secrets stay out
db2 = os.path.join(tmp, "two.db"); shutil.copy(dbp, db2)
c = sqlite3.connect(db2); [c.execute("delete from " + t) for t in ("attachment_map", "record_pass_markers", "target_mappings", "source_records")]; c.commit(); c.close()
objects(db2, Fake())
put("attA2", b"CHANGED AFTER DOWNLOAD")
fk = Fake(fail_upload_once=True)
res = atts(db2, fk, "--send", name="bad")["result"]
fails = open(os.path.join(out("bad"), "attachments_failures.csv"), encoding="utf-8-sig").read()
check("a file that changed after its download is not uploaded, and a failing upload is listed with a scrubbed reason", res["failed"] == 2 and "no longer matches" in fails and KEY not in fails and "abc.def.ghi" not in fails and len(fk.of("/docu")) == 0, (res["failed"], fails))
put("attA2", FILES["attA2"])
fk = Fake()
res = atts(db2, fk, "--send", name="retry")["result"]
check("the next run retries downloaded files that failed and uploads them", res["retried"] == 2 and res["uploaded"] == 2 and len(fk.of("/docu")) == 2, res)

# ---- an attachment waits for its record's Core object
db3 = os.path.join(tmp, "three.db"); shutil.copy(dbp, db3)
c = sqlite3.connect(db3); [c.execute("delete from " + t) for t in ("attachment_map", "record_pass_markers", "target_mappings", "source_records")]; c.commit(); c.close()
rm_c = sqlite3.connect(db3); rm_c.row_factory = sqlite3.Row; rm.register(rm_c, "appL", "tReq", ["recR1", "recR2", "recR3"]); rm_c.close()
fk = Fake()
res = atts(db3, fk, "--send", name="wait")["result"]
check("with no Core object yet nothing is uploaded and the files wait", fk.reqs == [] and res["waiting"] == 2 and res["uploaded"] == 0)

# ---- the HTTP layer: a busy server is retried and the whole file is sent again; a redirect is refused
import loadconfig as lc, workfronthttp as wh
class Seq(object):
    def __init__(self, replies): self.replies, self.bodies = list(replies), []
    def open(self, req, timeout=None):
        self.bodies.append(req.data.read()); r = self.replies.pop(0)
        if r[0] >= 300: raise urllib.error.HTTPError(req.full_url, r[0], "x", {}, io.BytesIO(b"{}"))
        return Resp(r[0], r[1])
cr = lc.credentials({"workfront": {"core": {"sandbox1": {"base_url": "https://acme.sb01.workfront.com", "api_path": "/attask/api/v21.0", "api_key": KEY}}}}, "core", "sandbox1")
fpath = os.path.join(ex, "attachments", "attA1.pdf")
sq = Seq([(503, {}), (200, {"data": {"handle": "HX"}})])
check("a busy server is retried and the full file is sent again", wh.Http(cr, opener=sq, sleep=lambda x: None).upload("/upload", "uploadedFile", "a.pdf", fpath) == {"data": {"handle": "HX"}} and len(sq.bodies) == 2 and sq.bodies[0].split(b"--", 2)[0] == b"" and FILES["attA1"] in sq.bodies[0] and FILES["attA1"] in sq.bodies[1])
sq = Seq([(302, {})])
try:
    wh.Http(cr, opener=sq, sleep=lambda x: None).upload("/upload", "uploadedFile", "a.pdf", fpath); red = ""
except wh.HttpError as e:
    red = str(e)
check("a redirect is not followed, so the key stays with the configured address", "redirect" in red and len(sq.bodies) == 1)

# ---- guards
check("a real production run needs the exact phrase", "CREATE IN PRODUCTION" in (refused(["attachments", "--db", db1, "--base", "appL", "--config", cfgp, "--send", "--core-env", "production"], opener=Fake()) or ""))
sys.exit(0 if ok else 1)
