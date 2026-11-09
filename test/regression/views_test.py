import json, urllib.request, sqlite3, csv, io, sys, copy
B="http://127.0.0.1:8765"; ok=True
def check(n,c,x=""):
    global ok; ok&=bool(c); print(("PASS " if c else "FAIL ")+n, "" if c else x)
def get(p): return urllib.request.urlopen(B+p).read().decode()
def post(p,b):
    req=urllib.request.Request(B+p,method="POST",data=json.dumps(b).encode(),headers={"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req) as r: return json.loads(r.read())
    except urllib.error.HTTPError as e: return json.loads(e.read())
post("/api/db/create",{"path":"v.db"}); post("/api/db/connect",{"path":"v.db"})
r=post("/api/schema/ingest",{"base_id":"appSampleBase001","source":"api"})
check("API ingest reports 3 views", r.get("views")==3, r)
sch=json.loads(get("/api/schema")); tb={t["table_name"]:t for b in sch["bases"] for t in b["tables"]}
cu=tb["Customers"]
check("Customers primary field name + 2 views in order", cu["primary_field_name"]=="Name" and [v["view_type"] for v in cu["views"]]==["grid","form"], cu.get("views"))
check("include=visibleFieldIds sent: grid has 2 visible fields, form none reported", cu["views"][0]["visible_field_count"]==2 and cu["views"][1]["visible_field_count"] is None, cu["views"])
check("kanban with empty visible list = 0", tb["Orders"]["views"][0]["visible_field_count"]==0)
check("table without views says details captured, no views", tb["Products"]["details_captured"] and tb["Products"]["views"]==[])
c=sqlite3.connect("v.db"); c.row_factory=sqlite3.Row
v=dict(c.execute("select * from airtable_views where view_id='viwCust0000001'").fetchone())
check("view row: ids json + lossless view_json", json.loads(v["visible_field_ids"])==["fldCustName0001","fldCustEmail001"] and json.loads(v["view_json"])["name"]=="All customers" and v["view_position"]==0)
check("form view stored without visible ids", c.execute("select visible_field_ids from airtable_views where view_id='viwCust0000002'").fetchone()[0] is None)
tr=dict(c.execute("select * from airtable_tables where table_id='tblCustomers0001'").fetchone())
check("table row: primary field id + table_json", tr["primary_field_id"]=="fldCustName0001" and json.loads(tr["table_json"])["name"]=="Customers" and "fields" not in json.loads(tr["table_json"]))
doc=json.load(open("test/sample_schema_field_options.json"))
r=post("/api/schema/ingest",{"base_id":"appOptions00001","source":"json","schema_json":json.dumps(doc)})
check("JSON ingest reports 5 views", r.get("views")==5, r)
sch=json.loads(get("/api/schema")); tb={t["table_name"]:t for b in sch["bases"] for t in b["tables"]}
ol=tb["Options Lab"]
check("description + view types", ol["description"].startswith("Lab table") and [v["view_type"] for v in ol["views"]]==["grid","form","calendar","timeline"], ol["views"])
post("/api/schema/ingest",{"base_id":"appOptions00001","source":"json","schema_json":json.dumps(doc)})
check("re-ingest keeps 5 view rows", c.execute("select count(*) from airtable_views where base_id='appOptions00001'").fetchone()[0]==5)
d2=copy.deepcopy(doc); d2["tables"][0]["views"]=[x for x in d2["tables"][0]["views"] if x["id"]!="viwOpt000000004"]
post("/api/schema/ingest",{"base_id":"appOptions00001","source":"json","schema_json":json.dumps(d2)})
sch=json.loads(get("/api/schema")); tb={t["table_name"]:t for b in sch["bases"] for t in b["tables"]}
st={v["view_name"]:v["stale"] for v in tb["Options Lab"]["views"]}
check("removed view kept and flagged", st=={"Grid view":False,"Intake form":False,"Launch calendar":False,"Roadmap":True}, st)
d3=copy.deepcopy(d2)
for t in d3["tables"]: t.pop("views",None)
post("/api/schema/ingest",{"base_id":"appOptions00001","source":"json","schema_json":json.dumps(d3)})
sch=json.loads(get("/api/schema")); tb={t["table_name"]:t for b in sch["bases"] for t in b["tables"]}
check("no views key => nothing newly flagged", not any(v["stale"] for v in tb["Options Lab"]["views"]), tb["Options Lab"]["views"])
post("/api/schema/ingest",{"base_id":"appOptions00001","source":"json","schema_json":json.dumps(d2)})
rows=list(csv.DictReader(io.StringIO(get("/api/export/views.csv"))))
check("views.csv: header and rows (stale excluded)", list(rows[0])==["base_id","table_id","table_name","view_id","view_name","view_type","view_position","visible_field_ids"] and len(rows)==7 and not any(x["view_name"]=="Roadmap" for x in rows), len(rows))
check("views.csv base filter", len(list(csv.DictReader(io.StringIO(get("/api/export/views.csv?base_id=appSampleBase001")))))==3)
tbl=list(csv.DictReader(io.StringIO(get("/api/export/tables.csv"))))
check("tables.csv: 12 tables, description present", len(tbl)==12 and any(x["table_description"].startswith("Lab table") for x in tbl), len(tbl))
rep=get("/api/report.html?mode=collapse")
check("report 3b lists views, types, visible field names", "3b. Tables and views" in rep and "Launch calendar" in rep and "Not reported" in rep and "Signup form" in rep and "Name, Email" in rep)
check("no token in responses", "patMOCK" not in rep and "patMOCK" not in get("/api/schema"))
sys.path.insert(0,"."); import server
calls=[]
def fake(**kw):
    calls.append(kw.get("query"))
    if kw.get("query"): raise server.ApiError("rejected", 422, "x")
    return {"tables":[{"id":"tblFb0000000001","name":"Fb","primaryFieldId":"fldFb0000000001","fields":[{"id":"fldFb0000000001","name":"N","type":"singleLineText"}],"views":[{"id":"viwFb0000000001","name":"G","type":"grid"}]}]}
server.STATE["db_path"]="fb.db"; server.api_db_create({"path":"fb.db"}); server.api_db_connect({"path":"fb.db"})
server.api_call=fake; server.get_token=lambda: "x"
r=server.api_schema_ingest({"base_id":"appFallback0001","source":"api"})
check("include rejected (422) => retried without it, views still captured", len(calls)==2 and calls[0] and calls[1] is None and r["views"]==1, (calls,r))
print("ALL PASS" if ok else "SOME FAILED")
