import json, urllib.request, urllib.error, sqlite3, csv, io, sys, re, copy
B = "http://127.0.0.1:8765"
ok = True


def check(n, c, x=""):
    global ok
    ok &= bool(c)
    print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:400])


def get(p, raw=False):
    try:
        with urllib.request.urlopen(B + p) as r:
            body = r.read()
            return (r.status, dict(r.getheaders()), body) if raw else body.decode()
    except urllib.error.HTTPError as e:
        return (e.code, dict(e.headers), e.read()) if raw else e.read().decode()


def post(p, b):
    req = urllib.request.Request(B + p, method="POST", data=json.dumps(b).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


post("/api/db/create", {"path": "a.db"})
post("/api/db/connect", {"path": "a.db"})
post("/api/schema/ingest", {"base_id": "appSampleBase001", "source": "api"})
post("/api/schema/ingest", {"base_id": "appSampleBase002", "source": "api"})
ORD, CUS = "tblOrders000001", "tblCustomers0001"
TOTAL, ONUM, NAME, ACTIVE, EMAIL = "fldOrdTotal0001", "fldOrdNumber001", "fldCustName0001", "fldCustActive01", "fldCustEmail001"
WIKI = "https://en.wikipedia.org/wiki/Brand_(marketing)"


def cond(fid, op, val="", source=""):
    return {"type": "condition", "left": {"kind": "field", "field_id": fid, "source": source}, "operator": op, "value": val}


def grp(junction, *items):
    return {"type": "group", "junction": junction, "items": list(items)}


def spec_parts():
    trigger = {"trigger_type": "record_matches", "table_id": ORD, "view_id": "", "watched_field_ids": [TOTAL], "schedule_text": "",
               "description_md": "Fires for **large** orders. See [Policy](%s)." % WIKI,
               "conditions": grp("AND", cond(TOTAL, "greaterThan", "100"),
                                 grp("OR", cond(ONUM, "isNotEmpty"), {"type": "condition", "left": {"kind": "text", "text": "Today"}, "operator": "isAfter", "value": "2026-01-01"}))}
    steps = [
        {"id": "s1", "type": "action", "action_type": "find_records", "title": "Find the customer", "description_md": "", "table_id": CUS,
         "field_refs": [], "settings": [], "conditions": grp("AND", cond(NAME, "isNotEmpty"))},
        {"id": "s2", "type": "conditional", "title": "Route by status", "description_md": "", "branches": [
            {"id": "b1", "type": "branch", "label": "VIP", "is_else": False, "conditions": grp("AND", cond(ACTIVE, "equals", "checked", source="s1")), "steps": [
                {"id": "s3", "type": "action", "action_type": "update_record", "title": "Mark the order", "description_md": "", "table_id": ORD,
                 "record_source": "", "field_refs": [{"field_id": TOTAL, "role": "set", "value": "0"}], "settings": []},
                {"id": "s4", "type": "action", "action_type": "send_email", "title": "Email the customer", "description_md": "", "table_id": "",
                 "field_refs": [{"field_id": EMAIL, "role": "use", "value": "To address"}],
                 "settings": [{"name": "Subject", "format": "rich", "value_md": "Thanks, see [our terms](https://example.com/terms)"},
                              {"name": "Body", "format": "rich", "value_md": "Hello **there**\n\n- one\n- two"}]}]},
            {"id": "b2", "type": "branch", "label": "", "is_else": False, "conditions": grp("OR", cond(EMAIL, "contains", "@corp", source="s1"), cond(NAME, "isEmpty", "", source="s1")), "steps": []},
            {"id": "b3", "type": "branch", "label": "", "is_else": True, "steps": [
                {"id": "s5", "type": "action", "action_type": "run_script", "title": "Log it", "description_md": "", "table_id": "", "field_refs": [],
                 "settings": [{"name": "Script", "format": "plain", "value": "// [x](javascript:alert(1))\nif (a < b && c) { run(); }\n<script>alert(1)</script>"}]}]}]},
        {"id": "s6", "type": "repeat", "title": "Each customer", "description_md": "", "over_text": "", "over_source": "s1", "table_id": CUS, "steps": [
            {"id": "s7", "type": "action", "action_type": "update_record", "title": "Activate", "description_md": "", "table_id": CUS, "record_source": "s6",
             "field_refs": [{"field_id": ACTIVE, "role": "set", "value": "checked"}], "settings": []}]}]
    return trigger, steps


def body(mut=None, **auto):
    trigger, steps = spec_parts()
    a = {"automation_id": "auto-large-orders", "automation_name": "Large order follow-up", "base_id": "appSampleBase001",
         "target_scenario_name": "Fusion: Large orders", "description_md": "Intro [Docs](https://example.com/docs)", "enabled_in_source": True}
    a.update(auto)
    b = {"automation": a, "trigger": trigger, "steps": steps, "note": "first"}
    if mut:
        mut(b)
    return b


def bad(label, mut, needle, **auto):
    r = post("/api/automations/save", body(mut, **auto))
    check("rejected: " + label, "error" in r and needle.lower() in r["error"].lower(), r)


bad("find condition given a record source", lambda b: b["steps"][0]["conditions"]["items"][0]["left"].update(source="s1"), "no record source")
bad("find condition field from another table", lambda b: b["steps"][0]["conditions"]["items"][0]["left"].update(field_id=TOTAL), "record it is read from")
bad("condition reads a step that comes later", lambda b: b["steps"][1]["branches"][0]["conditions"]["items"][0]["left"].update(source="s6"), "earlier step")
bad("condition source is not a record-producing step", lambda b: b["steps"][1]["branches"][1]["conditions"]["items"][0]["left"].update(source="s5"), "earlier step")
bad("field from the wrong table for its source", lambda b: b["steps"][1]["branches"][0]["conditions"]["items"][0]["left"].update(field_id=TOTAL), "record it is read from")
bad("else branch that is not last", lambda b: b["steps"][1]["branches"].append(copy.deepcopy(b["steps"][1]["branches"][1]) | {"id": "b9"}), "only the last")
bad("branch with no conditions", lambda b: b["steps"][1]["branches"][1].update(conditions=grp("AND")), "no conditions")
bad("unsafe link in a setting", lambda b: b["steps"][1]["branches"][0]["steps"][1]["settings"][0].update(value_md="[x](javascript:alert(1))"), "link")
bad("update step names a source that is not earlier", lambda b: b["steps"][1]["branches"][0]["steps"][0].update(record_source="s7"), "record to update")
bad("repeat over something that is not a find step", lambda b: b["steps"][2].update(over_source="s3"), "find records")
bad("create/update/find step without a table", lambda b: b["steps"][0].update(table_id=""), "needs a table")
bad("table not in the base", lambda b: b["steps"][0].update(table_id="tblNope000000001"), "not in base")
bad("field not in the base", lambda b: b["steps"][1]["branches"][0]["steps"][0]["field_refs"][0].update(field_id="fldNope00000001"), "not in base")
bad("trigger watches a field of another table", lambda b: b["trigger"].update(watched_field_ids=[NAME]), "trigger's table")
bad("trigger conditions given a step source", lambda b: b["trigger"]["conditions"]["items"][0]["left"].update(source="s1"), "trigger record")
bad("record_matches trigger without conditions", lambda b: b["trigger"].update(conditions=grp("AND")), "at least one condition")
bad("step without a title", lambda b: b["steps"][0].update(title=""), "no title")
bad("repeated step id", lambda b: b["steps"][1]["branches"][0]["steps"][0].update(id="s1"), "repeated id")
bad("bad automation key", lambda b: None, "automation key", automation_id="bad/key")
bad("unknown trigger type", lambda b: b["trigger"].update(trigger_type="nope"), "trigger type")

r = post("/api/automations/save", body())
check("valid automation saved as version 1", r.get("version_no") == 1 and not r.get("unchanged") and r.get("items") == 11, r)
check("identical content creates no new version", post("/api/automations/save", body()).get("unchanged") is True)


def rename(b):
    b["steps"][2]["title"] = "Each matching customer"


r3 = post("/api/automations/save", dict(body(rename), note="renamed repeat"))
check("a change creates version 2", r3.get("version_no") == 2, r3)

# ---------- stored data ----------
c = sqlite3.connect("a.db")
c.row_factory = sqlite3.Row
sid = c.execute("select spec_id from automation_specs where version_no=1").fetchone()[0]
paths = [(x["path"], x["item_kind"], x["step_type"]) for x in c.execute("select path,item_kind,step_type from automation_items where spec_id=? order by item_order", (sid,))]
check("items in document order with paths (trigger, steps, branches, nested steps)",
      paths == [("0", "trigger", "record_matches"), ("1", "step", "find_records"), ("2", "step", "conditional"), ("2.1", "branch", "branch"),
                ("2.1.1", "step", "update_record"), ("2.1.2", "step", "send_email"), ("2.2", "branch", "branch"), ("2.3", "branch", "branch"),
                ("2.3.1", "step", "run_script"), ("3", "step", "repeat"), ("3.1", "step", "update_record")] + [], paths)
lg = [tuple(x) for x in c.execute("select owner_item_id,node_path,node_kind,junction,controlling_field_id,source_item_id,left_text,operator,value "
                                  "from automation_logic where spec_id=? and owner_item_id in ('trigger','b2') order by owner_item_id,node_path", (sid,))]
check("nested groups stored as paths (root AND, inner OR, text-left condition)",
      lg == [("b2", "0", "group", "OR", None, None, None, None, None), ("b2", "0.0", "condition", None, EMAIL, "s1", None, "contains", "@corp"),
             ("b2", "0.1", "condition", None, NAME, "s1", None, "isEmpty", ""),
             ("trigger", "0", "group", "AND", None, None, None, None, None), ("trigger", "0.0", "condition", None, TOTAL, None, None, "greaterThan", "100"),
             ("trigger", "0.1", "group", "OR", None, None, None, None, None), ("trigger", "0.1.0", "condition", None, ONUM, None, None, "isNotEmpty", ""),
             ("trigger", "0.1.1", "condition", None, None, None, "Today", "isAfter", "2026-01-01")], lg)
refs = {(x["item_id"], x["role"], x["field_id"]) for x in c.execute("select * from automation_field_refs where spec_id=?", (sid,))}
check("field refs: condition, watch, set, use", {("trigger", "condition", TOTAL), ("trigger", "watch", TOTAL), ("s3", "set", TOTAL), ("s4", "use", EMAIL),
                                                    ("b1", "condition", ACTIVE), ("s7", "set", ACTIVE), ("s1", "condition", NAME)} <= refs, refs)
links = [(x["item_id"], x["text_role"], x["link_text"], x["url"]) for x in c.execute("select * from automation_links where spec_id=? order by item_id,text_role,ordinal", (sid,))]
check("links kept verbatim, including settings and the trigger; plain script text yields none",
      links == [("automation", "description", "Docs", "https://example.com/docs"), ("s4", "setting:Subject", "our terms", "https://example.com/terms"),
                ("trigger", "description", "Policy", WIKI)], links)
sp = json.loads(c.execute("select spec_json from automation_specs where spec_id=?", (sid,)).fetchone()[0])
check("spec JSON keeps the script verbatim and the Markdown with its parsed blocks",
      sp["format"] == "automation-spec/1" and "javascript:alert(1)" in sp["steps"][1]["branches"][2]["steps"][0]["settings"][0]["value"] and sp["trigger"]["description_blocks"][0]["runs"][1]["b"])

# ---------- list / spec ----------
al = json.loads(get("/api/automations/list"))["automations"]
check("list shows latest version, count, trigger type and target scenario", len(al) == 1 and al[0]["latest_version"] == 2 and al[0]["versions"] == 2 and al[0]["trigger_type"] == "record_matches" and al[0]["target_scenario_name"] == "Fusion: Large orders", al)
v1 = json.loads(get("/api/automations/spec?automation_id=auto-large-orders&version=1"))
lt = json.loads(get("/api/automations/spec?automation_id=auto-large-orders"))
check("older version intact, latest is version 2", v1["spec"]["steps"][2]["title"] == "Each customer" and lt["spec"]["steps"][2]["title"] == "Each matching customer" and [x["version_no"] for x in lt["versions"]] == [2, 1])
st, _h, _b = get("/api/automations/spec?automation_id=nope", raw=True)
check("unknown automation is a 404", st == 404, st)

# ---------- field usage ----------
u = json.loads(get("/api/automations/usage?base_id=appSampleBase001"))
roles = lambda fid, rows=None: sorted({r["role"] for r in (rows or u["rows"]) if r["field_id"] == fid})
check("Total: tested in a condition, watched, written", roles(TOTAL) == ["condition", "set", "watch"], roles(TOTAL))
check("Email: used (in an email) and tested in a condition", roles(EMAIL) == ["condition", "use"], roles(EMAIL))
check("Active: tested and written", roles(ACTIVE) == ["condition", "set"], roles(ACTIVE))
check("latest version only by default", {r["version_no"] for r in u["rows"]} == {2})
check("all versions includes version 1", {r["version_no"] for r in json.loads(get("/api/automations/usage?all_versions=1"))["rows"]} == {1, 2})
check("filters: role, field id, text, automation",
      all(r["role"] == "watch" for r in json.loads(get("/api/automations/usage?role=watch"))["rows"])
      and {r["field_id"] for r in json.loads(get("/api/automations/usage?field_id=%s" % ONUM))["rows"]} == {ONUM}
      and {r["field_name"] for r in json.loads(get("/api/automations/usage?q=email"))["rows"]} == {"Email"}
      and not json.loads(get("/api/automations/usage?automation_id=other"))["rows"])
rows = list(csv.DictReader(io.StringIO(get("/api/export/automation_usage.csv"))))
check("usage csv header and row count", list(rows[0]) == ["base_id", "automation_id", "automation_name", "version_no", "field_id", "field_name", "table_name", "data_type",
                                                          "role", "item_path", "item_title", "detail", "workfront_dev_field_id", "workfront_prod_field_id"] and len(rows) == len(u["rows"]), len(rows))
lk = list(csv.DictReader(io.StringIO(get("/api/export/automation_links.csv"))))
check("links csv: path, text, address", [(x["path"], x["text_role"], x["link_text"], x["url"]) for x in lk]
      == [("automation", "description", "Docs", "https://example.com/docs"), ("0", "description", "Policy", WIKI), ("2.1.2", "setting:Subject", "our terms", "https://example.com/terms")], lk)

# ---------- reports ----------
src = get("/api/automations/report.html?automation_id=auto-large-orders")
pos = [src.find(x) for x in ["Find the customer", "Route by status", "Else if", "Else</span>", "Each matching customer"]]
check("source report: trigger, then steps in order with branches", src.find("Trigger") < pos[0] and all(p >= 0 for p in pos) and pos == sorted(pos), pos)
check("source report: nested ALL/ANY groups and a non-field condition", "<b>ALL</b> of these are true" in src and "<b>ANY</b> of these are true" in src and "<b>Today</b> is after" in src)
check("source report: field written, field used, record sources", "Set <b>Total</b>" in src and "Uses <b>Email</b>" in src and "Updates the trigger record" in src and "Each customer" not in src)
check("source report: link text with its real address; rich text rendered",
      'our terms</a> <span class="url">(https://example.com/terms)</span>' in src and "<strong>there</strong>" in src and WIKI in src)
check("source report: script kept verbatim in a code block and escaped", "&lt;script&gt;alert(1)&lt;/script&gt;" in src and "<script>alert(1)" not in src and "if (a &lt; b &amp;&amp; c)" in src)
check("source report: target scenario, version history, sign-off", "Fusion: Large orders" in src and "Version history" in src and "6. Review" in src)
old = get("/api/automations/report.html?automation_id=auto-large-orders&version=1")
check("older version report shows the older title", "Each customer" in old and "Each matching customer" not in old and "(this report)" in old)
tg = get("/api/automations/report.html?automation_id=auto-large-orders&target=DEV")
check("target report with no crosswalk says not ready and flags UNMAPPED", "Not ready to rebuild" in tg and "UNMAPPED: Total" in tg and "Airtable:" in tg, tg[:300])
for fid, w, nm, typ in [(TOTAL, "wfTotal", "Order Value", "Currency"), (ONUM, "wfNum", "Order Ref", "Single Line Text"), (NAME, "wfCust", "Customer Name", "Single Line Text"),
                        (ACTIVE, "wfAct", "Is Active", "Boolean / Checkbox"), (EMAIL, "wfMail", "Contact Email", "Email")]:
    c.execute("insert into workfront_fields values(?,?,?,?,?,?,?,?,current_timestamp)", ("DEV|" + w, "DEV", "WS", "Order" if fid in (TOTAL, ONUM) else "Contact", None, w, nm, typ))
    c.execute("insert into field_crosswalk(airtable_base_id,airtable_table_name,airtable_field_id,airtable_field_name,workfront_dev_field_id) values('appSampleBase001','x',?,?,?)", (fid, nm, w))
c.commit()
tg = get("/api/automations/report.html?automation_id=auto-large-orders&target=DEV")
check("target report: every field mapped; target names replace source names",
      "Every field in this automation is mapped" in tg and "UNMAPPED" not in tg and "Order Value" in tg and "Customer Name" in tg and "Contact Email" in tg and "Is Active" in tg, tg[:500])
check("target report: tables show the Workfront record type learned from the mappings", "Workfront: WS / Contact" in tg and "Workfront: WS / Order" in tg)
check("target report: links, text and scenario carried over", WIKI in tg and "https://example.com/terms" in tg and "Fusion: Large orders" in tg)
stt = json.loads(get("/api/automations/report/status?automation_id=auto-large-orders"))
check("status counts: DEV all mapped, PROD none", stt["unmapped"] == {"dev": 0, "prod": 5}, stt)
ur = json.loads(get("/api/automations/usage?field_id=%s" % TOTAL))["rows"]
check("usage rows carry the crosswalk's Workfront field ids", ur and all(r["workfront_dev_field_id"] == "wfTotal" and r["workfront_prod_field_id"] == "" for r in ur), ur)
stx, hx, bx = get("/api/automations/report.html?automation_id=auto-large-orders&download=1", raw=True)
check("html download file name", re.match(r'attachment; filename="Automation_auto-large-orders_v2_Airtable_\d{8}-\d{4}\.html"', hx.get("Content-Disposition", "")), hx)
stm, hm, bm = get("/api/automations/report.mhtml?automation_id=auto-large-orders&target=PROD", raw=True)
check("mhtml download", stm == 200 and "_PROD_" in hm.get("Content-Disposition", "") and bm.startswith(b"From:") and b"multipart/related" in bm, hm)
st, _h, _b = get("/api/automations/report.html?automation_id=auto-large-orders&target=BAD", raw=True)
check("bad target rejected", st == 400, st)
check("no token in any automation response", "patMOCK" not in src + tg + get("/api/automations/list"))

# ---------- other automations ----------
other = lambda key, base="appSampleBase001": {"automation": {"automation_id": key, "automation_name": "Other", "base_id": base}, "trigger": {"trigger_type": "scheduled", "schedule_text": "Every Monday 9am"}, "steps": []}
check("a scheduled automation with no steps saves", post("/api/automations/save", other("auto-other")).get("version_no") == 1)
r = post("/api/automations/save", other("auto-other", "appSampleBase002"))
check("automation key cannot be reused by another base", "error" in r and "already used" in r["error"], r)
check("list now has two automations", len(json.loads(get("/api/automations/list"))["automations"]) == 2)
st, _h, _b = get("/api/automations/save", raw=True)
check("save is POST only", st == 404, st)
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
