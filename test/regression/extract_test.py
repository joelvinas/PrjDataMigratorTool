import json, urllib.request, sqlite3, os, glob, time, hashlib, sys, shutil
B="http://127.0.0.1:8765"; ok=True
def check(n,c,x=""):
    global ok; ok&=bool(c); print(("PASS " if c else "FAIL ")+n, "" if c else str(x)[:300])
def get(p): return urllib.request.urlopen(B+p).read().decode()
def post(p,b):
    req=urllib.request.Request(B+p,method="POST",data=json.dumps(b).encode(),headers={"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req) as r: return json.loads(r.read())
    except urllib.error.HTTPError as e: return json.loads(e.read())
def wait():
    for _ in range(300):
        j=json.loads(get("/api/profile/status"))["job"]
        if j and j["status"]!="running": return j
        time.sleep(.2)
    raise SystemExit("job did not finish")
D="data_extract"
shutil.rmtree(D,ignore_errors=True)
post("/api/db/create",{"path":"ex.db"}); post("/api/db/connect",{"path":"ex.db"})
post("/api/schema/ingest",{"base_id":"appSampleBase001","source":"api"})
cust=[t for b in json.loads(get("/api/schema"))["bases"] for t in b["tables"] if t["table_name"]=="Customers"][0]["table_id"]
# 1. toggle OFF: nothing on disk
post("/api/profile/start",{"scope":"table","table_id":cust,"cap":1000}); j=wait()
check("toggle off: job says not saving, nothing on disk", j["save_records"] is False and not os.path.exists(D) and j["tables"][0]["saved"] is False, j["save_records"])
check("toggle off: files list is empty", json.loads(get("/api/extract/files"))["files"]==[])
# 2. toggle ON, one table
post("/api/profile/start",{"scope":"table","table_id":cust,"cap":1000,"save_records":True}); j=wait()
t=j["tables"][0]
check("toggle on: saved, 3 pages, 3 API calls (one pass serves both profile and file)", t["saved"] and t["pages"]==3 and j["api"]["calls"]==3 and j["save_records"] is True, (t,j["api"]))
path="%s/appSampleBase001/%s.json"%(D,cust); doc=json.load(open(path,encoding="utf-8"))
ids=[r["id"] for r in doc["records"]]
check("file: format, ids, count, unique records", doc["format"]=="airtable-table-extract/1" and doc["record_count"]==250==len(doc["records"]) and len(set(ids))==250 and doc["table_name"]=="Customers" and doc["captured_at"].endswith("Z"), doc.keys())
check("file: values keyed by field ID; fields list names them", all(k.startswith("fld") for r in doc["records"] for k in r["fields"]) and {f["field_name"] for f in doc["fields"]}>={"Name","Email"} and all(set(r)>= {"id","createdTime","fields"} for r in doc["records"]))
mf=json.load(open(D+"/manifest.json")); e=mf["tables"][0]
check("manifest: count, bytes, sha256 match the file; no values", e["record_count"]==250 and e["bytes"]==os.path.getsize(path) and e["sha256"]==hashlib.sha256(open(path,"rb").read()).hexdigest() and "records" not in e)
fl=json.loads(get("/api/extract/files"))
check("files endpoint: matches profile, folder shown", fl["files"][0]["check"]=="matches profile" and fl["total_records"]==250 and fl["folder"].endswith("data_extract"), fl)
# 3. values must not leak anywhere else
vals=set()
for r in doc["records"]:
    for v in r["fields"].values():
        if isinstance(v,str) and len(v)>=7: vals.add(v)
vals=sorted(vals)[:400]
blob=b""
for f in glob.glob("ex.db*"): blob+=open(f,"rb").read()
blob+=open("server.log","rb").read() if os.path.exists("server.log") else b""
texts=[get(p) for p in ("/api/calllog","/api/runbook","/api/profile/status","/api/extract/files","/api/schema","/api/profile/results","/api/status","/api/report.html?mode=collapse","/api/depgraph?mode=literal")]
leaks=[v for v in vals if v.encode() in blob or any(v in x for x in texts)]
check("record values (%d distinct strings) appear nowhere but the JSON file"%len(vals), len(vals)>20 and not leaks, leaks[:3])
# 4. whole base
post("/api/profile/start",{"scope":"base","base_id":"appSampleBase001","cap":1000,"save_records":True}); j=wait()
check("base run: all tables saved", all(x["saved"] for x in j["tables"]) and j["status"]=="done" and len(j["tables"])==10, [ (x["table_name"],x["status"]) for x in j["tables"]])
fl=json.loads(get("/api/extract/files"))
check("files == tables, records total equals profile, all match", len(fl["files"])==10 and all(f["check"]=="matches profile" for f in fl["files"]) and fl["total_records"]==sum(x["records"] for x in j["tables"]), [f["check"] for f in fl["files"]])
oi=[f for f in fl["files"] if f["table_name"]=="OrderItems"][0]; check("paged table: OrderItems 301 records", oi["record_count"]==301)
# 5. errors base: Forbidden fails cleanly, others saved
err=open("test/sample_schema_errors.json").read()
post("/api/schema/ingest",{"base_id":"appSampleBase002","source":"api"})
post("/api/profile/start",{"scope":"base","base_id":"appSampleBase002","cap":1000,"save_records":True}); j=wait()
st={x["table_name"]:(x["status"],x["saved"]) for x in j["tables"]}
check("failed table writes no file and leaves no .tmp; others saved", st["Forbidden"]==("failed",False) and st["Flaky"]==("done",True) and st["WideCardinality"]==("done",True) and not glob.glob(D+"/**/*.tmp",recursive=True) and not [f for f in glob.glob(D+"/appSampleBase002/*.json") if "Forbidden" in open(f).read(400)], st)
# 6. cancel leaves no tmp
post("/api/profile/start",{"scope":"all","cap":1000,"save_records":True}); time.sleep(.3); post("/api/profile/cancel",{}); j=wait()
check("cancel: no .tmp left behind", j["status"] in("cancelled","done") and not glob.glob(D+"/**/*.tmp",recursive=True), j["status"])
# 7. delete
r=post("/api/extract/delete",{"base_id":"appSampleBase002"}); fl=json.loads(get("/api/extract/files"))
check("delete one base: its files and manifest entries gone, other base kept", r["removed"]>=1 and fl["files"] and all(f["base_id"]=="appSampleBase001" for f in fl["files"]) and not os.path.isdir(D+"/appSampleBase002"), r)
r=post("/api/extract/delete",{}); check("delete all: folder and manifest removed", not os.path.exists(D) and json.loads(get("/api/extract/files"))["files"]==[], r)
r=post("/api/extract/delete",{"base_id":"../x"}); check("bad base id refused", "error" in r, r)
# 8. toggle off again after all that: still nothing written
post("/api/profile/start",{"scope":"table","table_id":cust,"cap":1000,"save_records":False}); wait()
check("toggle off again: nothing on disk", not os.path.exists(D))
# 9. in-process: failure keeps old file, duplicate ids, unsafe ids
sys.path.insert(0,"."); import server
server.STATE["db_path"]="ip.db"; server.api_db_create({"path":"ip.db"}); server.api_db_connect({"path":"ip.db"})
sch={"tables":[{"id":"tblIp000000001","name":"Ip","primaryFieldId":"fldIp0000000001","fields":[{"id":"fldIp0000000001","name":"Name","type":"singleLineText"}]}]}
server.upsert_schema("appIp0000000001",server.parse_schema(sch),"json")
server.get_token=lambda:"x"
mode={"m":"good"}
def fake(**kw):
    off=(kw.get("query") or {}).get("offset")
    mk=lambda a,b:[{"id":"rec%05d"%i,"createdTime":"2026-01-01T00:00:00.000Z","fields":{"fldIp0000000001":"value-%05d"%i}} for i in range(a,b)]
    if mode["m"]=="good": return {"records":mk(0,100),"offset":"o2"} if not off else {"records":mk(100,150)}
    if mode["m"]=="fail": 
        if not off: return {"records":mk(0,100),"offset":"o2"}
        raise server.ApiError("boom",500,"x")
    if mode["m"]=="dup": return {"records":mk(0,100),"offset":"o2"} if not off else {"records":mk(50,60)}
server.api_call=fake
def run():
    server.api_profile_start({"scope":"table","table_id":"tblIp000000001","cap":1000,"save_records":True})
    for _ in range(100):
        j=server.JOB["current"]
        if j.status!="running": return j
        time.sleep(.05)
run(); p=server._extract_path("appIp0000000001","tblIp000000001"); good=open(p,"rb").read()
check("in-process: good run writes 150 records", json.loads(good)["record_count"]==150)
mode["m"]="fail"; j=run()
check("failed run (500 on page 2): table failed, OLD file byte-identical, no .tmp", j.tables[0]["status"]=="failed" and open(p,"rb").read()==good and not os.path.exists(p+".tmp"), j.tables[0])
mode["m"]="dup"; j=run()
check("duplicate record IDs: table failed with clear message, old file kept, no .tmp", j.tables[0]["status"]=="failed" and "twice" in j.tables[0]["error"] and open(p,"rb").read()==good and not os.path.exists(p+".tmp"), j.tables[0])
try: server._extract_path("../etc","tblX"); bad=False
except server.AppError: bad=True
check("path traversal refused", bad)
shutil.rmtree(D,ignore_errors=True)
print("ALL PASS" if ok else "SOME FAILED")
