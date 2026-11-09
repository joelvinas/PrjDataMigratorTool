import json, urllib.request, os, shutil, re, hashlib, sqlite3, time
import os as _o
exec(open(_o.path.join(_o.path.dirname(_o.path.abspath(__file__)),"extract_test.py")).read().split("D=\"data_extract\"")[0])
D="data_extract"; shutil.rmtree(D,ignore_errors=True)
post("/api/db/create",{"path":"at.db"}); post("/api/db/connect",{"path":"at.db"})
post("/api/schema/ingest",{"base_id":"appSampleBase001","source":"api"})
cust=[t for b in json.loads(get("/api/schema"))["bases"] for t in b["tables"] if t["table_name"]=="Customers"][0]["table_id"]
r=post("/api/profile/start",{"scope":"table","table_id":cust,"download_attachments":True})
check("download without save_records is refused", "error" in r or "message" in r, r)
post("/api/profile/start",{"scope":"table","table_id":cust,"save_records":True}); j=wait()
check("save only: no attachments folder", not os.path.exists(D+"/appSampleBase001/attachments") and j["download_files"] is False)
r=post("/api/profile/start",{"scope":"table","table_id":cust,"save_records":True,"download_attachments":True}); j=wait()
t=j["tables"][0]
check("job: downloading, 3 API calls (still one pass)", j["download_files"] and j["api"]["calls"]==3 and t["pages"]==3, (j["api"],t))
ad=D+"/appSampleBase001/attachments"; files=sorted(os.listdir(ad))
check("125 files downloaded, 1 failed (expired link)", t["files"]==125 and t["file_failed"]==1 and len(files)==125, (t,len(files)))
check("names are <id>.<ext>; no .part left", all(re.match(r"^attR\d+A\d\.(jpg|pdf)$",f) for f in files), [f for f in files if not re.match(r"^attR\d+A\d\.(jpg|pdf)$",f)][:5])
check("extension follows filename (photo-4.JPG -> jpg; Report..PDF -> pdf); .jfif/noext fall back to MIME", "attR4A0.jpg" in files and any(f.endswith(".pdf") for f in files))
doc=json.load(open(D+"/appSampleBase001/%s.json"%cust,encoding="utf-8"))
idx=json.load(open(D+"/appSampleBase001/%s.attachments.json"%cust,encoding="utf-8"))
check("index: 126 entries, 1 failed with reason and no link", len(idx["attachments"])==126 and sum(e["status"]=="failed" for e in idx["attachments"])==1 and "http" not in json.dumps(idx), idx["attachments"][0])
ok_all=True
for e in idx["attachments"]:
    if e["status"]=="failed": continue
    p=D+"/appSampleBase001/"+e["file"]
    data=open(p,"rb").read()
    ok_all&= hashlib.sha256(data).hexdigest()==e["sha256"] and len(data)==e["bytes"]==e["size"]
check("every file: size equals Airtable's size, sha256 matches index", ok_all)
check("index ties file to record+field", all(e["record_id"].startswith("rec") and e["field_id"].startswith("fld") for e in idx["attachments"]))
mf=json.load(open(D+"/manifest.json"))["tables"][0]
check("manifest summary", mf["attachments"]["files"]==125 and mf["attachments"]["failed"]==1, mf.get("attachments"))
fl=json.loads(get("/api/extract/files"))
check("files endpoint totals", fl["total_attachment_files"]==125 and fl["total_attachment_failed"]==1 and fl["total_attachment_bytes"]>0)
hits=json.loads(urllib.request.urlopen("http://127.0.0.1:9100/_file_hits").read())
check("token never sent to file host (no 400s), each file fetched once", all(v==1 for k,v in hits.items() if k!="attR8A1"), {k:v for k,v in hits.items() if v!=1})
check("expired link not retried", hits.get("attR8A1")==1, hits.get("attR8A1"))
# rerun: existing files kept, not refetched
post("/api/profile/start",{"scope":"table","table_id":cust,"save_records":True,"download_attachments":True}); j=wait()
hits2=json.loads(urllib.request.urlopen("http://127.0.0.1:9100/_file_hits").read())
check("rerun reuses saved files (no new fetches except the failed one)", j["tables"][0]["files"]==125 and sum(hits2.values())-sum(hits.values())==1, sum(hits2.values())-sum(hits.values()))
# leaks
blob=b""
for f in ["at.db","server.log"]:
    blob+=open(f,"rb").read()
for ep in ["/api/profile/status","/api/calllog","/api/runbook","/api/extract/files","/api/schema","/api/profile/results"]:
    blob+=get(ep).encode()
check("no file URLs, attachment ids or file names in db/log/browser responses", b"/files/" not in blob and b"attR4A0" not in blob and b"photo-4" not in blob and b"patMOCK" not in blob)
# delete
r=post("/api/extract/delete",{})
check("delete removes files, folder, index", r["removed"]>=127 and not os.path.exists(D), r)
print("ALL PASS" if ok else "SOME FAILED")
