import json, urllib.request, urllib.parse, sqlite3, email, re, sys, subprocess, os
B="http://127.0.0.1:8765"; ok=True
def check(n,c,x=""):
    global ok; ok&=bool(c); print(("PASS " if c else "FAIL ")+n, "" if c else x)
def get(p):
    r=urllib.request.urlopen(B+p); return r.status, dict(r.headers), r.read()
def post(p,b):
    req=urllib.request.Request(B+p,method="POST",data=json.dumps(b).encode(),headers={"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req) as r: return json.loads(r.read())
    except urllib.error.HTTPError as e: return json.loads(e.read())

post("/api/db/connect",{"path":"t.db"})
post("/api/depgraph/recompute",{"mode":"collapse"})
st,h,b=get("/api/report.html?mode=collapse"); html=b.decode()
check("html served as text/html", st==200 and h["Content-Type"].startswith("text/html"))
conn=sqlite3.connect("t.db")
fields=[r[0] for r in conn.execute("select field_id from airtable_fields")]; tables=[r[0] for r in conn.execute("select distinct table_id from airtable_fields")]
check("every field ID in the report (%d)"%len(fields), all(f in html for f in fields), [f for f in fields if f not in html][:3])
check("every table ID in the report", all(t in html for t in tables))
for s in ["1. About this report","2. Schema source and date","3. Schema inventory","4. Load order","5. Link fields and second-pass loading","6. Dependency graph","7. How the order is derived"]:
    check("section: "+s, s in html)
check("order of sections: provenance, inventory, load order, graph", html.index("2. Schema source")<html.index("3. Schema inventory")<html.index("4. Load order")<html.index("6. Dependency graph"))
check("provenance: API base says pulled", "Pulled from the Airtable Metadata API" in html and "appSampleBase002" in html)
check("provenance: json base says provided", "Provided as a JSON document" in html)
ts=conn.execute("select max(updated_at) from airtable_fields where base_id='appSampleBase002'").fetchone()[0][:19]
check("captured timestamp matches DB (%s)"%ts, ts+" UTC" in html)
check("link-handling mode shown", "Resolve two-way link pairs" in html)
check("inline svg graph, one node per table (13)", html.count("<svg")==1 and len(re.findall(r"<rect x=",html))==13)
check("self-link marked", "↻" in html and "Self-link" in html)
check("second-pass rows present", 'class="second"' in html and "Yes" in html)
check("oriented pair text", "Tasks is the child of Projects" in html)
check("no token / no db path", "patMOCK" not in html and "/run5" not in html and "t.db" in html)
check("in sync after recompute (no warning)", "differs from the order computed here" not in html)
# literal mode while stored matrix is collapse => warning
st,h,b=get("/api/report.html?mode=literal"); check("out-of-sync warning when mode differs from saved", "differs from the order computed here" in b.decode())
# determinism: same report twice (strip generated time)
strip=lambda s: re.sub(r"Generated [^b]*by the", "Generated X by the", s)
a=strip(get("/api/report.html?mode=collapse")[2].decode()); c=strip(get("/api/report.html?mode=collapse")[2].decode()); check("deterministic: two reports identical apart from time", a==c)
st,h,b=get("/api/report.html?mode=collapse&download=1"); check("html download header", "attachment" in h["Content-Disposition"] and h["Content-Disposition"].endswith('.html"'))
# MHTML
st,h,raw=get("/api/report.mhtml?mode=collapse")
check("mhtml download header", "attachment" in h["Content-Disposition"] and h["Content-Disposition"].endswith('.mhtml"'), h)
msg=email.message_from_bytes(raw)
check("mhtml is multipart/related", msg.get_content_type()=="multipart/related" and msg.get_param("type")=="text/html")
parts=msg.get_payload(); check("one text/html part, quoted-printable", len(parts)==1 and parts[0].get_content_type()=="text/html" and parts[0]["Content-Transfer-Encoding"]=="quoted-printable")
dec=parts[0].get_payload(decode=True).decode("utf-8").replace("\r\n","\n")
check("mhtml round-trips to the HTML exactly (modulo time)", strip(dec)==a, (len(dec),len(a)))
check("mhtml is CRLF throughout", b"\n" not in raw.replace(b"\r\n",b""))
open("report.mhtml","wb").write(raw); open("report.html","wb").write(get("/api/report.html?mode=collapse")[2])
# fallback: audit entries removed => "Not recorded"
conn.execute("delete from runbook_logs where failing_component like 'schema_ingest:%'"); conn.commit()
check("old DB without audit rows: source says not recorded, date still shown", "Not recorded" in get("/api/report.html")[2].decode() and ts+" UTC" in get("/api/report.html")[2].decode())
# HTML injection through names
evil={"tables":[{"id":"tblEvil00000001","name":"<script>alert(1)</script>&\"'","fields":[{"id":"fldEvil000000001","name":"<img src=x onerror=alert(2)>","type":"singleLineText"},
  {"id":"fldEvil000000002","name":"Link","type":"multipleRecordLinks","options":{"linkedTableId":"tblEvil00000001"}}]}]}
r=post("/api/schema/ingest",{"base_id":"appEvilBase0001","source":"json","schema_json":json.dumps(evil)}); check("evil schema ingested", r.get("tables")==1, r)
html=get("/api/report.html")[2].decode()
check("names are HTML-escaped in the report", "<script>alert(1)" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html and "<img src=x" not in html)
# empty DB error
r=post("/api/db/create",{"path":"empty.db"}); 
try: get("/api/report.html"); empty_ok=True
except Exception as e: empty_ok=False
# switch back to evil-free? just verify the earlier behaviour with brand new db
post("/api/db/connect",{"path":"empty.db"})
try: urllib.request.urlopen(B+"/api/report.html"); check("empty DB gives clear error", False)
except urllib.error.HTTPError as e: check("empty DB gives clear error", e.code==400 and "no schema" in json.loads(e.read())["error"].lower())
print("REPORT TESTS", "ALL PASS" if ok else "SOME FAILED"); sys.exit(0 if ok else 1)
