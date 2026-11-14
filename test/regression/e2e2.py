import json, sys, time, urllib.request, sqlite3, subprocess, os
B="http://127.0.0.1:8765"
def call(m, p, body=None):
    req=urllib.request.Request(B+p, method=m, data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type":"application/json"} if m=="POST" else {})
    try:
        with urllib.request.urlopen(req) as r: return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())
ok=True
def check(name, cond, extra=""):
    global ok; ok &= bool(cond); print(("PASS " if cond else "FAIL ")+name, extra if not cond else "")
sch=open("test/sample_schema.json").read(); err=open("test/sample_schema_errors.json").read()

st,r=call("GET","/api/status"); check("token configured, not leaked", r["token_configured"] and "patMOCK" not in json.dumps(r))
st,r=call("POST","/api/db/connect",{"path":"t.db"}); check("nonexistent db offers create", r["exists"] is False)
st,r=call("POST","/api/db/create",{"path":"t.db"}); check("create db ready", r["ready"] and len(r["created_tables"])==21)
st,r=call("POST","/api/db/connect",{"path":"t.db"}); check("reconnect ready", r["ready"] and r["foreign_keys"])
# missing table detection
c=sqlite3.connect("t.db"); c.execute("DROP TABLE form_links"); c.commit(); c.close()
st,r=call("POST","/api/db/connect",{"path":"t.db"}); check("missing table reported", r["missing_tables"]==["form_links"] and not r["ready"])
st,r=call("POST","/api/db/create",{"path":"t.db"}); check("missing table recreated", r["ready"] and r["created_tables"]==["form_links"])

st,r=call("POST","/api/schema/ingest",{"base_id":"appSampleBase001","source":"json","schema_json":sch}); check("json ingest", st==200 and r["tables"]==10 and r["added"]==r["fields"], r)
st,r=call("POST","/api/schema/ingest",{"base_id":"appSampleBase001","source":"json","schema_json":sch}); check("re-ingest idempotent", r["added"]==0 and r["updated"]==r["fields"] and r["no_longer_in_source"]==0)
c=sqlite3.connect("t.db"); n=c.execute("select count(*) from airtable_fields").fetchone()[0]; check("field count stable", n==r["fields"])
lk=c.execute("select linked_table_id, inverse_link_field_id from airtable_fields where field_id='fldArtTags00001'").fetchone(); check("link + inverse stored", lk==("tblTags00000001","fldTagArticles01"), lk)
pf={a:b for a,b in c.execute("select field_id, prefers_single_record_link from airtable_fields where field_id in ('fldTaskProject01','fldProjTasks0001','fldCustName0001','fldOrdCustomer1')")}
check("prefers_single_record_link stored (1/0/NULL)", pf=={"fldTaskProject01":1,"fldProjTasks0001":0,"fldCustName0001":None,"fldOrdCustomer1":1}, pf)
c.close()

st,r=call("POST","/api/depgraph/recompute",{"mode":"literal"})
m={x["table_name"]:x for x in r["matrix"]}
print("literal order:", [(x["load_order_sequence"],x["table_name"],x["dependency_depth"],x["in_cycle"]) for x in r["matrix"]])
check("literal: 3 cycles", len(r["cycles"])==3, [c["tables"] for c in r["cycles"]])
check("literal: self-link listed", [s["table_name"] for s in r["self_links"]]==["Customers"])
check("literal: self-link ignored as parent", m["Customers"]["parent_table_ids"]==[] and m["Customers"]["dependency_depth"]==0)
check("order parents-first", m["Customers"]["load_order_sequence"]<m["Orders"]["load_order_sequence"]<m["OrderItems"]["load_order_sequence"] and m["Products"]["load_order_sequence"]<m["OrderItems"]["load_order_sequence"])
check("depth OrderItems=2", m["OrderItems"]["dependency_depth"]==2)
check("runbook rows for cycles", r["runbook_rows_written"]==3)
st,r2=call("POST","/api/depgraph/recompute",{"mode":"literal"}); check("recompute: no dup matrix, no dup runbook", len(r2["matrix"])==10 and r2["runbook_rows_written"]==0)
c=sqlite3.connect("t.db"); check("matrix rows == tables", c.execute("select count(*) from dependency_matrix").fetchone()[0]==10)
seqs=[x[0] for x in c.execute("select load_order_sequence from dependency_matrix order by 1")]; check("sequence 1..10 unique", seqs==list(range(1,11)))
check("runbook component", c.execute("select count(*) from runbook_logs where failing_component='dependency_graph' and source_system='Airtable' and status='OPEN'").fetchone()[0]==3); c.close()
st,r=call("POST","/api/depgraph/recompute",{"mode":"collapse"}); 
check("collapse: Dept/Emp and the Articles/Tags pair (no direction) are the cycles", sorted(tuple(c["tables"]) for c in r["cycles"])==[("Articles","Tags"),("Departments","Employees")], r["cycles"])
check("collapse: 2 pairs listed", len(r["inverse_pairs"])==2)
pp={(p["table_a"],p["table_b"]):p for p in r["inverse_pairs"]}
check("pair Projects/Tasks oriented: Tasks child of Projects", pp[("Projects","Tasks")]["child_table"]=="Tasks" and pp[("Projects","Tasks")]["parent_table"]=="Projects", pp)
check("pair Articles/Tags left unordered (many-to-many)", pp[("Articles","Tags")]["child_table"] is None)
mm={x["table_name"]:x for x in r["matrix"]}
check("collapse: Projects loads before Tasks, Tasks depth 1", mm["Projects"]["load_order_sequence"]<mm["Tasks"]["load_order_sequence"] and mm["Tasks"]["dependency_depth"]==1 and mm["Tasks"]["parent_table_ids"]==["tblProjects00001"], mm["Tasks"])
check("collapse: Articles/Tags stay a cycle (each lists the other as parent)", mm["Articles"]["parent_table_ids"]==["tblTags00000001"] and mm["Tags"]["parent_table_ids"]==["tblArticles0001"])
mm2={x["table_name"]:x for x in call("POST","/api/depgraph/recompute",{"mode":"literal"})[1]["matrix"]}
check("literal: Projects/Tasks is a cycle (hint ignored)", mm2["Tasks"]["in_cycle"] and mm2["Projects"]["in_cycle"])
st,r=call("POST","/api/depgraph/recompute",{"mode":"collapse"})
st,v=call("GET","/api/depgraph?mode=collapse"); check("matrix in sync", v["matrix_in_sync"])
st,v=call("GET","/api/depgraph?mode=literal"); check("out-of-sync detected on mode change", not v["matrix_in_sync"])
st,r=call("POST","/api/depgraph/recompute",{"mode":"literal"})

# stale detection: drop a field from the source
d=json.loads(sch); d["tables"][2]["fields"]=d["tables"][2]["fields"][:1]  # Products loses Price
st,r=call("POST","/api/schema/ingest",{"base_id":"appSampleBase001","source":"json","schema_json":json.dumps(d)}); check("field removal flagged, not deleted", r["no_longer_in_source"]==1)
st,v=call("GET","/api/schema"); fl=[f for b in v["bases"] for t in b["tables"] for f in t["fields"] if f["stale"]]; check("stale flag in view", [f["field_id"] for f in fl]==["fldProdPrice001"])
c=sqlite3.connect("t.db"); check("stale field still in db", c.execute("select count(*) from airtable_fields where field_id='fldProdPrice001'").fetchone()[0]==1); c.close()
# wrong base id clash
st,r=call("POST","/api/schema/ingest",{"base_id":"appOtherBase001","source":"json","schema_json":sch}); check("base id clash rejected", st==400 and "already belong" in r["error"], r)
st,r=call("POST","/api/schema/ingest",{"base_id":"bogus","source":"json","schema_json":sch}); check("bad base id message", st==400)
st,r=call("POST","/api/schema/ingest",{"base_id":"appSampleBase001","source":"json","schema_json":"{not json"}); check("bad json message", st==400 and "not valid JSON" in r["error"])
# restore full schema
call("POST","/api/schema/ingest",{"base_id":"appSampleBase001","source":"json","schema_json":sch})

# API fetch from mock + errors base
st,r=call("POST","/api/schema/ingest",{"base_id":"appSampleBase002","source":"api"}); check("api schema fetch", st==200 and r["tables"]==3, r)
st,r=call("POST","/api/schema/ingest",{"base_id":"appNoSuchBase01","source":"api"}); check("404 fail fast w/ plain message", st==502 and "404" in r["error"], r)
c=sqlite3.connect("t.db"); rows=c.execute("select attempt,http_status,success,endpoint,error_message from api_call_log where purpose='schema_fetch' order by call_id").fetchall(); print(rows)
check("404 logged once (no retry)", sum(1 for x in rows if x[1]==404)==1 and "token" not in json.dumps(rows).lower().replace("tokens","")); c.close()

# profiling
t0=time.time()
st,r=call("POST","/api/profile/start",{"scope":"base","base_id":"appSampleBase001","cap":50000}); check("profile start", st==200, r)
st2,r2=call("POST","/api/profile/start",{"scope":"all"}); check("second start rejected while running", st2==400 and "already" in r2["error"], r2)
while True:
    time.sleep(0.5); st,j=call("GET","/api/profile/status"); 
    if j["job"]["status"]!="running": break
job=j["job"]; print("job", job["status"], job["api"], round(time.time()-t0,1),"s")
check("job done, no failures", job["status"]=="done" and all(t["status"]=="done" for t in job["tables"]), [t for t in job["tables"] if t["status"]!="done"])
byname={t["table_name"]:t for t in job["tables"]}
check("paging: Customers 250 recs in 3 pages", byname["Customers"]["records"]==250 and byname["Customers"]["pages"]==3)
check("paging: OrderItems 301 recs in 4 pages", byname["OrderItems"]["records"]==301 and byname["OrderItems"]["pages"]==4)
expected_calls=sum(t["pages"] for t in job["tables"]); check("api call count == pages", job["api"]["calls"]==expected_calls and job["api"]["failure"]==0)
st,res=call("GET","/api/profile/results?table_id=tblCustomers0001"); R={x["field_id"]:x for x in res["rows"]}
# expected, computed independently from the same generator
sys.path.insert(0,"test"); import mock_airtable as M
tab=next(t for t in M.load("appSampleBase001")["tables"] if t["id"]=="tblCustomers0001")
recs=[M.make_record(tab,i) for i in range(250)]
def exp(fid, ftype):
    nul=0; dist=set()
    for r in recs:
        v=r["fields"].get(fid)
        if ftype=="checkbox" and v is None: v=False
        if v is None or v=="" or v==[]: nul+=1; continue
        if ftype=="multipleAttachments": v=[a["id"] for a in v]
        dist.add(json.dumps(v,sort_keys=True))
    return nul,len(dist)
for f in tab["fields"]:
    n,cd=exp(f["id"],f["type"]); x=R[f["id"]]
    check("Customers.%s null=%d card=%d"%(f["name"],n,cd), x["row_count"]==250 and x["null_count"]==n and x["cardinality"]==cd, x)
check("checkbox never null, 2 distinct", R["fldCustActive01"]["null_count"]==0 and R["fldCustActive01"]["cardinality"]==2)
st,res=call("GET","/api/profile/results?table_id=tblOrders000001"); R2={x["field_id"]:x for x in res["rows"]}
check("zero counted as real value (Total)", R2["fldOrdTotal0001"]["null_count"]==0, R2["fldOrdTotal0001"])
c=sqlite3.connect("t.db"); ts=c.execute("select count(distinct updated_at) from airtable_fields where base_id='appSampleBase001'").fetchone()[0]
check("profiling left updated_at alone (stale logic intact)", ts==1, ts); c.close()
# stale field untouched by profiling
st,res=call("GET","/api/profile/results?table_id=tblProducts0001")

# failures + retry + cap
st,r=call("POST","/api/profile/start",{"scope":"base","base_id":"appSampleBase002","cap":500}); 
while True:
    time.sleep(0.5); st,j=call("GET","/api/profile/status")
    if j["job"]["status"]!="running": break
job=j["job"]; bn={t["table_name"]:t for t in job["tables"]}
print({k:(v["status"],v["pages"],v["error"]) for k,v in bn.items()}, job["api"])
check("Forbidden failed fast, run continued", bn["Forbidden"]["status"]=="failed" and "403" in bn["Forbidden"]["error"] and bn["WideCardinality"]["status"]=="done" and bn["Flaky"]["status"]=="done")
c=sqlite3.connect("t.db")
fl=c.execute("select attempt,http_status,success from api_call_log where table_id='tblFlaky0000001' order by call_id").fetchall(); print("flaky attempts", fl)
check("429 x2 then success, attempts numbered", [x[0] for x in fl][:3]==[1,2,3] and [x[1] for x in fl][:3]==[429,429,200] and fl[2][2]==1)
fb=c.execute("select attempt,http_status from api_call_log where table_id='tblForbidden0001'").fetchall(); check("403 not retried", fb==[(1,403)], fb)
check("runbook row for 403", c.execute("select count(*) from runbook_logs where failing_component='profile_page:tblForbidden0001' and status='OPEN'").fetchone()[0]==1)
card=c.execute("select field_name, cardinality, row_count, cardinality_is_capped from airtable_fields where table_id='tblWideCard00001' order by field_name").fetchall(); print(card)
check("cap: capped field stores cap + flag, low-card exact", card==[("Bucket",4,1000,0),("Unique Key",500,1000,1)], card)
st,res=call("GET","/api/profile/results?table_id=tblWideCard00001"); u=[x for x in res["rows"] if x["field_name"]=="Unique Key"][0]; check("UI flag capped w/ cap", u["cardinality_capped"] and u["cardinality"]==500, u)
# previous good stats not wiped by failed table: Forbidden stats remain default
c.close()
st,lg=call("GET","/api/calllog?failures=1"); check("calllog failures filter", lg["rows"] and all(x["success"]==0 for x in lg["rows"]) and lg["summary"]["failure"]>=3)
st,lg=call("GET","/api/calllog?table_id=tblFlaky0000001&purpose=profile_page"); check("calllog table+purpose filter", all(x["table_id"]=="tblFlaky0000001" for x in lg["rows"]) and len(lg["rows"])==4, len(lg["rows"]))

# security
req=urllib.request.Request(B+"/api/status", headers={"Host":"evil.example"}); 
try: urllib.request.urlopen(req); check("bad Host rejected", False)
except urllib.error.HTTPError as e: check("bad Host rejected", e.code==403)
req=urllib.request.Request(B+"/api/db/connect", method="POST", data=b"{}", headers={"Content-Type":"text/plain"})
try: urllib.request.urlopen(req); check("non-json POST rejected", False)
except urllib.error.HTTPError as e: check("non-json POST rejected", e.code==415)
req=urllib.request.Request(B+"/api/db/connect", method="POST", data=b"{}", headers={"Content-Type":"application/json","Origin":"https://evil.example"})
try: urllib.request.urlopen(req); check("cross-origin POST rejected", False)
except urllib.error.HTTPError as e: check("cross-origin POST rejected", e.code==403)
# token never in db
tok=b"patMOCK.0000000000000000000000"; blob=open("t.db","rb").read(); check("token not in database file", tok not in blob)
st,r=call("POST","/api/profile/start",{"scope":"table","table_id":"tblWideCard00001"})
while True:
    time.sleep(0.4); st,j=call("GET","/api/profile/status")
    if j["job"]["status"]!="running": break
check("default cap is 1,000", j["job"]["cap"]==1000, j["job"]["cap"])
c=sqlite3.connect("t.db"); card=c.execute("select field_name,cardinality,cardinality_is_capped from airtable_fields where table_id='tblWideCard00001' order by field_name").fetchall()
check("1,000 distinct == cap is still exact (not capped); rerun cleared the flag", card==[("Bucket",4,0),("Unique Key",1000,0)], card); c.close()
# SQL files
import importlib.util
spec=importlib.util.spec_from_file_location("server","server.py"); srv=importlib.util.module_from_spec(spec); spec.loader.exec_module(srv)
check("sql/DatabaseSchemaLibrary.sql == embedded DDL", open("sql/DatabaseSchemaLibrary.sql",encoding="utf-8").read()==srv.DDL)
orig=open(__import__("os").path.join(__import__("os").path.dirname(__import__("os").path.abspath(__file__)),"original_ddl.sql"),encoding="utf-8").read()
a=sqlite3.connect(":memory:"); a.executescript(orig)
oldcols={t:[x[1] for x in a.execute('pragma table_info(%s)'%t)] for t in srv.EXPECTED_TABLES}
changed={t for t in srv.EXPECTED_TABLES if oldcols[t]!=srv.EXPECTED_COLUMNS[t][:len(oldcols[t])] and set(oldcols[t])!=set(srv.EXPECTED_COLUMNS[t])}
check("only airtable_fields differs and only the tables from migrations 003 to 007 are new", {x for x in srv.EXPECTED_TABLES if oldcols[x] and set(oldcols[x])!=set(srv.EXPECTED_COLUMNS[x])}=={"airtable_fields"} and {x for x in srv.EXPECTED_TABLES if not oldcols[x]}=={"airtable_tables","airtable_views","airtable_attachment_stats","form_specs","form_items","form_logic","form_links","automation_specs","automation_items","automation_logic","automation_field_refs","automation_links","field_names","table_names","name_events"})
check("only the approved columns were added", set(srv.EXPECTED_COLUMNS["airtable_fields"])-set(oldcols["airtable_fields"])=={"prefers_single_record_link","cardinality_is_capped"}|set(srv.OPTION_COLUMNS) and not set(oldcols["airtable_fields"])-set(srv.EXPECTED_COLUMNS["airtable_fields"]))
# old database: app refuses, migration fixes it, data survives
old=sqlite3.connect("old.db"); old.executescript(orig); old.execute("insert into airtable_fields(base_id,table_id,table_name,field_id,field_name,data_type,row_count,null_count,cardinality) values('appOld00001','tblOld','Old','fldOld','F','singleLineText',7,1,3)"); old.commit(); old.close()
st,r=call("POST","/api/db/connect",{"path":"old.db"})
check("old DB not ready; message points to migration", not r["ready"] and r["column_problems"][0]["missing_columns"] and "apply_migrations" in r["message"], r)
old.close(); subprocess.run([sys.executable,"sql/apply_migrations.py","old.db"],check=True,capture_output=True)
st,r=call("POST","/api/db/connect",{"path":"old.db"}); check("old DB ready after migration", r["ready"], r)
oc=sqlite3.connect("old.db"); names={x[0] for x in oc.execute("select name from sqlite_master where type='table'")}; oc.close()
check("migrations 005 and 006 dropped the empty placeholders and added the form and automation tables", not ({"form_sections","form_fields","automation_catalog","automation_steps"}&names) and {"form_specs","form_items","form_logic","form_links","automation_specs","automation_items","automation_logic","automation_field_refs","automation_links"}<=names, names)
old=sqlite3.connect("old.db"); row=old.execute("select row_count,null_count,cardinality,cardinality_is_capped,prefers_single_record_link from airtable_fields").fetchone(); old.close()
check("migration kept data, flag defaults 0, hint NULL", row==(7,1,3,0,None), row)
# ---- multi-page site: fixed list of pages, nothing outside it is served ----
import http.client, re
def raw(path, method="GET"):
    c=http.client.HTTPConnection("127.0.0.1",8765); c.request(method,path); r=c.getresponse(); body=r.read(); h=dict(r.getheaders()); c.close(); return r.status,h,body
PAGES={"/":"setup","/setup":"setup","/stage1":"stage1","/stage2":"stage2","/stage3":"stage3","/log":"log"}
for path,key in PAGES.items():
    st,h,body=raw(path); t=body.decode("utf-8")
    check("page %s served as HTML with the nav mount and its page key"%path, st==200 and h["Content-Type"].startswith("text/html") and 'id="sidenav"' in t and 'data-page="%s"'%key in t, (st,h.get("Content-Type")))
for path in ["/style.css","/common.js","/setup.js","/stage1.js","/stage2.js","/stage3.js","/editor-kit.js","/log.js"]:
    st,h,body=raw(path); check("asset %s served"%path, st==200 and len(body)>200, st)
st,h,body=raw("/index.html"); check("/index.html redirects to the home page (Setup)", st==302 and h.get("Location")=="/", (st,h.get("Location")))
for path in ["/scripts.js","/server.py","/config.json","/README.md","/../server.py","/%2e%2e/server.py","/stage1/../server.py","/test/mock_airtable.py","/db/migration.db","/stage4","/stage1.html"]:
    st,h,body=raw(path); check("not served: %s"%path, st==404 and b"def " not in body and b"patMOCK" not in body, st)
st,h,body=raw("/stage1","POST"); check("POST to a page is rejected", st in (404,415), st)
# every page loads the shared script first; no page or script builds markup from data or carries a token
for f in ["setup.html","stage1.html","stage2.html","stage3.html","log.html"]:
    t=open(f,encoding="utf-8").read(); check("%s: shared nav, shared script, no inline script"%f, '<nav id="sidenav"' in t and t.index("/common.js")<t.rindex("</body>") and "<script>" not in t)
for f in ["common.js","setup.js","stage1.js","stage2.js","stage3.js","editor-kit.js","log.js"]:
    t=open(f,encoding="utf-8").read(); check("%s: no innerHTML use, no document.write and no token text"%f, not re.search(r"\.innerHTML|insertAdjacentHTML|document\.write|outerHTML", t) and "patMOCK" not in t and "Authorization" not in t)
t=open("stage1.html",encoding="utf-8").read(); check("Stage 1 holds steps 3 to 6 and no step 1 or 2", all('id="step%d"'%i in t for i in (3,4,5,6)) and 'id="step1"' not in t and 'id="step2"' not in t)
t=open("setup.html",encoding="utf-8").read(); check("Setup holds steps 1 and 2 only", 'id="step1"' in t and 'id="step2"' in t and 'id="step3"' not in t)
t=open("common.js",encoding="utf-8").read(); check("log is pinned at the bottom of the nav and Stage 4 is a planned slot", "NAV_BOTTOM" in t and '"/log"' in t and "Stage 4: Map and load" in t)
import re as _re
check("APP_VERSION matches EXPECTED_SERVER_VERSION", _re.search(r'APP_VERSION = "([^"]+)"', open("server.py",encoding="utf-8").read()).group(1)==_re.search(r'EXPECTED_SERVER_VERSION = "([^"]+)"', t).group(1))
print("ALL PASS" if ok else "SOME FAILED"); sys.exit(0 if ok else 1)
