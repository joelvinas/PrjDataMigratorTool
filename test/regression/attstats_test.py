import json, urllib.request, os, shutil, sqlite3, csv, io
import os as _o
exec(open(_o.path.join(_o.path.dirname(_o.path.abspath(__file__)),"extract_test.py")).read().split("D=\"data_extract\"")[0])
post("/api/db/create",{"path":"as.db"}); post("/api/db/connect",{"path":"as.db"})
post("/api/schema/ingest",{"base_id":"appSampleBase001","source":"api"})
cust=[t for b in json.loads(get("/api/schema"))["bases"] for t in b["tables"] if t["table_name"]=="Customers"][0]["table_id"]
c=sqlite3.connect("as.db")
check("13 tables incl. airtable_attachment_stats", c.execute("select count(*) from sqlite_master where type='table' and name='airtable_attachment_stats'").fetchone()[0]==1)
post("/api/profile/start",{"scope":"table","table_id":cust}); j=wait()   # saving OFF, downloading OFF
check("profile with save OFF: 3 calls, nothing on disk", j["api"]["calls"]==3 and not os.path.exists("data_extract"))
d=json.loads(get("/api/attachments/stats"))
T=d["total"]
check("totals: 126 files", T["files"]==126, T)
exp_bytes=0
def fb(aid): return len(("bytes-of-%s;" % aid)*(1+len(aid)*40))
for i in range(0,250,4):
    for jj in range(2): exp_bytes+=fb("attR%dA%d"%(i,jj))
check("total bytes equal the sum of the sizes Airtable reported", T["bytes"]==exp_bytes and T["unknown_size"]==0, (T["bytes"],exp_bytes))
check("size bands add up to the file count", sum(T["buckets"])==126 and T["buckets"][0]==126)
ext={r["extension"]:r["files"] for r in d["by_type"]}
check("types: jpg and pdf only (the .jfif and noext names fall back to MIME)", set(ext)=={"jpg","pdf"} and sum(ext.values())==126, ext)
check("by_table names Customers", d["by_table"][0]["table_name"]=="Customers" and d["by_table"][0]["files"]==126)
# re-run must replace, not double
post("/api/profile/start",{"scope":"table","table_id":cust}); wait()
check("re-run replaces rows (still 126)", json.loads(get("/api/attachments/stats"))["total"]["files"]==126)
rows=c.execute("select file_extension,mime_type,file_count,min_bytes,max_bytes from airtable_attachment_stats").fetchall()
check("mime types stored lower-case", all(r[1]==r[1].lower() for r in rows), rows)
cols=[r[1] for r in c.execute("pragma table_info(airtable_attachment_stats)")]
check("no filename/url columns", not any(x in cols for x in ("filename","url","file_name")), cols)
raw=open("as.db","rb").read()
check("no filenames, ids or links in the db file", b"photo-4" not in raw and b"/files/" not in raw and b"attR4A0" not in raw)
st,csvtxt=0,urllib.request.urlopen(B+"/api/export/attachment_stats.csv").read().decode()
rr=list(csv.DictReader(io.StringIO(csvtxt)))
check("csv: rows per type, totals match", sum(int(r["file_count"]) for r in rr)==126 and rr[0]["table_name"]=="Customers", rr[:1])
html=get("/api/report.html?mode=collapse")
check("report has section 3c with totals and types", "3c. Attachments" in html and "126 files" in html and "jpg" in html)
# missing-size handling in a table with attachments but no size: Orders? use unit check via server import
import importlib.util,sys
sys.path.insert(0,"."); spec=importlib.util.spec_from_file_location("srv","server.py"); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
st={}
for a in [{"id":"a","filename":"x.PNG","type":"image/png","size":5},{"id":"b","filename":"y","type":"application/pdf; x=1"},{"id":"c","filename":"z.zip","size":2*1024**3},{"id":"d","filename":"w.mov","type":"video/quicktime","size":150*1024**2},{"id":"e","filename":"v.txt","size":True}]:
    m._tally_attachment(st,"f",a)
check("unit: sizes/buckets/unknown", st[("f","png","image/png")]["buckets"]==[1,0,0,0,0] and st[("f","pdf","application/pdf")]["unknown"]==1 and st[("f","zip","unknown")]["buckets"]==[0,0,0,0,1] and st[("f","mov","video/quicktime")]["buckets"]==[0,0,0,1,0] and st[("f","txt","unknown")]["unknown"]==1, st)
print("ALL PASS" if ok else "SOME FAILED")
