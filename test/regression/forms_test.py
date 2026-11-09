import json, urllib.request, urllib.error, sqlite3, csv, io, sys, re
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


post("/api/db/create", {"path": "f.db"})
post("/api/db/connect", {"path": "f.db"})
post("/api/schema/ingest", {"base_id": "appSampleBase001", "source": "api"})
post("/api/schema/ingest", {"base_id": "appSampleBase002", "source": "api"})
NAME, EMAIL, ACTIVE, REF = "fldCustName0001", "fldCustEmail001", "fldCustActive01", "fldCustReferred1"
WIKI = "https://en.wikipedia.org/wiki/Brand_(marketing)"

# ---------- rich text parser ----------
pv = post("/api/forms/preview", {"texts": [
    "Hi **bold** and *it* and [Wiki](%s) end" % WIKI,
    "- one\n- two [bad](javascript:alert(1))\n\nline a\nline b",
    "**[Bold link](https://x.example/a?b=1&c=2)**",
    "\\*not italic\\* and [esc \\] text](mailto:a@b.co)"]})["previews"]
runs = pv[0]["blocks"][0]["runs"]
check("bold, italic and a link whose address has parentheses",
      [r["t"] for r in runs] == ["Hi ", "bold", " and ", "it", " and ", "Wiki", " end"] and runs[1]["b"] and runs[3]["i"] and runs[5]["href"] == WIKI, runs)
check("list items; an unsafe link address is refused with a warning",
      pv[1]["blocks"][0]["type"] == "ul" and len(pv[1]["blocks"][0]["items"]) == 2 and pv[1]["warnings"] and not pv[1]["links"], pv[1])
check("line break kept inside a paragraph", pv[1]["blocks"][1]["runs"][0]["t"] == "line a\nline b", pv[1]["blocks"][1])
check("bold inside a link is one link with the real address",
      pv[2]["links"] == [{"text": "Bold link", "url": "https://x.example/a?b=1&c=2"}] and pv[2]["blocks"][0]["runs"][0]["b"], pv[2])
check("escapes work and mailto links are allowed",
      pv[3]["blocks"][0]["runs"][0]["t"].startswith("*not italic* and ") and pv[3]["links"] == [{"text": "esc ] text", "url": "mailto:a@b.co"}], pv[3])


# ---------- a form ----------
def logic(*conds, junction="AND", skip_to=None):
    d = {"enabled": True, "junction": junction, "conditions": [{"field_id": f, "operator": o, "value": v} for f, o, v in conds]}
    if skip_to is not None:
        d["skip_to"] = skip_to
    return d


OFF = {"enabled": False, "junction": "AND", "conditions": []}


def spec():
    return [
        {"id": "i1", "type": "section", "title": "Contact", "description_md": "See **our brand** page: [Brand](%s)" % WIKI,
         "display_logic": OFF, "skip_logic": OFF, "items": [
             {"id": "i2", "type": "field", "field_id": NAME, "label": "Your name", "required": True, "help_md": "Use your *legal* name.", "display_logic": OFF},
             {"id": "i3", "type": "hidden_field", "field_id": REF, "preset_value": "Web", "preset_source": "URL parameter prefill_Referred", "note": "Set by the web form link"},
             {"id": "i4", "type": "field", "field_id": ACTIVE, "label": "", "required": False, "help_md": "", "display_logic": OFF},
             {"id": "i5", "type": "heading", "text": "Details", "display_logic": OFF}]},
        {"id": "i6", "type": "section", "title": "Follow-up", "description_md": "",
         "display_logic": logic((REF, "equals", "Web"), (NAME, "isNotEmpty", "")),
         "skip_logic": logic((ACTIVE, "equals", "unchecked"), skip_to="END"), "items": [
             {"id": "i7", "type": "field", "field_id": EMAIL, "label": "", "required": False, "help_md": "", "display_logic": logic((ACTIVE, "equals", "checked"))},
             {"id": "i8", "type": "help_text", "content_md": "Questions? [Email us](mailto:help@example.com)\n\n- first\n- second", "display_logic": OFF},
             {"id": "i9", "type": "text_block", "content_md": "<script>alert(1)</script> & done", "display_logic": OFF}]}]


def body(items=None, **form):
    f = {"form_id": "viwCust0000002", "form_name": "Signup form", "view_id": "viwCust0000002", "base_id": "", "table_id": "",
         "description_md": "Intro [Policy](https://example.com/p?x=1)"}
    f.update(form)
    return {"form": f, "items": items if items is not None else spec(), "note": "first"}


def bad(label, items=None, needle="", **form):
    r = post("/api/forms/save", body(items, **form))
    check("rejected: " + label, "error" in r and needle.lower() in r["error"].lower(), r)


c1 = spec(); c1[0]["items"][0]["display_logic"] = logic((EMAIL, "equals", "x"))
bad("logic using a field placed later", c1, "before")
c1b = spec(); c1b[0]["items"][0]["display_logic"] = logic((NAME, "equals", "x"))
bad("logic using the field itself", c1b, "before")
c2 = spec(); c2[0]["items"].append({"id": "i20", "type": "field", "field_id": NAME, "label": "", "required": False, "help_md": "", "display_logic": OFF})
bad("a field placed twice", c2, "already appears")
c3 = spec(); c3[0]["items"][0]["field_id"] = "fldOrdTotal0001"
bad("a field from another table", c3, "this form's table")
c4 = spec(); c4[1]["items"][1]["content_md"] = "[x](javascript:alert(1))"
bad("unsafe link address", c4, "link")
c5 = spec(); c5[1]["skip_logic"] = logic((ACTIVE, "equals", "x"), skip_to="i1")
bad("skip-to an earlier section", c5, "skip to")
c6 = spec(); c6[0]["items"][0]["field_id"] = "fldDoesNotExist1"
bad("unknown field", c6, "not in base")
c7 = spec(); c7[1]["display_logic"] = {"enabled": True, "junction": "AND", "conditions": []}
bad("enabled logic with no conditions", c7, "no conditions")
c8 = spec(); c8[0]["items"][3]["text"] = ""
bad("empty heading", c8, "no text")
bad("view not in the database", None, "not in the database", view_id="viwNope000000001")
bad("bad form key", None, "form key", form_id="bad/key", view_id="")

r = post("/api/forms/save", body())
check("valid form saved as version 1", r.get("version_no") == 1 and not r.get("unchanged") and r.get("items") == 9, r)
r2 = post("/api/forms/save", body())
check("saving identical content creates no new version", r2.get("unchanged") is True and r2.get("version_no") == 1, r2)
s2 = spec(); s2[0]["items"][0]["label"] = "Full name"
b2 = body(s2); b2["note"] = "renamed label"
r3 = post("/api/forms/save", b2)
check("a change creates version 2", r3.get("version_no") == 2, r3)

# ---------- stored data ----------
c = sqlite3.connect("f.db")
c.row_factory = sqlite3.Row
check("two version rows; base and table taken from the view",
      [tuple(x) for x in c.execute("select version_no,version_note,base_id,table_id from form_specs order by version_no")]
      == [(1, "first", "appSampleBase001", "tblCustomers0001"), (2, "renamed label", "appSampleBase001", "tblCustomers0001")])
sid = c.execute("select spec_id from form_specs where version_no=1").fetchone()[0]
paths = [(x["path"], x["item_type"]) for x in c.execute("select path,item_type from form_items where spec_id=? order by item_order", (sid,))]
check("items stored in document order with paths",
      paths == [("1", "section"), ("1.1", "field"), ("1.2", "hidden_field"), ("1.3", "field"), ("1.4", "heading"),
                ("2", "section"), ("2.1", "field"), ("2.2", "help_text"), ("2.3", "text_block")], paths)
h = dict(c.execute("select * from form_items where spec_id=? and item_id='i3'", (sid,)).fetchone())
check("hidden field keeps its preset value and source", h["preset_value"] == "Web" and h["preset_source"].startswith("URL parameter") and h["airtable_field_id"] == REF, h)
lg = [tuple(x) for x in c.execute("select owner_item_id,logic_kind,junction,cond_order,controlling_field_id,operator,value,skip_to_item_id "
                                  "from form_logic where spec_id=? order by owner_item_id,logic_kind,cond_order", (sid,))]
check("logic rows: section display, section skip (to END) and field display",
      lg == [("i6", "display", "AND", 0, REF, "equals", "Web", None), ("i6", "display", "AND", 1, NAME, "isNotEmpty", "", None),
             ("i6", "skip", "AND", 0, ACTIVE, "equals", "unchecked", "END"), ("i7", "display", "AND", 0, ACTIVE, "equals", "checked", None)], lg)
links = [(x["item_id"], x["text_role"], x["link_text"], x["url"]) for x in c.execute("select * from form_links where spec_id=? order by item_id,text_role,ordinal", (sid,))]
check("links kept verbatim (visible text and address), including the form description",
      links == [("form", "description", "Policy", "https://example.com/p?x=1"), ("i1", "description", "Brand", WIKI), ("i8", "content", "Email us", "mailto:help@example.com")], links)
sp = json.loads(c.execute("select spec_json from form_specs where spec_id=?", (sid,)).fetchone()[0])
check("spec JSON holds the Markdown and the parsed blocks",
      sp["items"][0]["description_md"].startswith("See **our brand**") and any(r["b"] for r in sp["items"][0]["description_blocks"][0]["runs"]) and sp["format"] == "form-spec/1")

# ---------- list / spec ----------
fl = json.loads(get("/api/forms/list"))["forms"]
check("list shows the latest version and version count", len(fl) == 1 and fl[0]["latest_version"] == 2 and fl[0]["versions"] == 2 and fl[0]["view_id"] == "viwCust0000002", fl)
v1 = json.loads(get("/api/forms/spec?form_id=viwCust0000002&version=1"))
lt = json.loads(get("/api/forms/spec?form_id=viwCust0000002"))
check("older version is intact, latest is version 2",
      v1["spec"]["items"][0]["items"][0]["label"] == "Your name" and lt["version_no"] == 2 and lt["spec"]["items"][0]["items"][0]["label"] == "Full name"
      and [x["version_no"] for x in lt["versions"]] == [2, 1])
st, _h, _b = get("/api/forms/spec?form_id=nope", raw=True)
check("unknown form is a 404", st == 404, st)
check("round trip keeps item ids and order", [i["id"] for i in lt["spec"]["items"][1]["items"]] == ["i7", "i8", "i9"])

# ---------- field usage ----------
u = json.loads(get("/api/forms/usage?base_id=appSampleBase001"))


def roles(fid, rows=None):
    return sorted({r["role"] for r in (rows or u["rows"]) if r["field_id"] == fid})


check("Name: placed, required, controls display logic", roles(NAME) == ["display_logic", "placed", "required"], roles(NAME))
check("Referred By: hidden/preset and controls display logic", roles(REF) == ["display_logic", "hidden_preset"], roles(REF))
check("Active: placed, controls display and skip logic", roles(ACTIVE) == ["display_logic", "placed", "skip_logic"], roles(ACTIVE))
check("latest version only by default (version 2 rows)", {r["version_no"] for r in u["rows"]} == {2})
ua = json.loads(get("/api/forms/usage?all_versions=1"))
check("all versions includes version 1", {r["version_no"] for r in ua["rows"]} == {1, 2})
check("filters: role, field id, text",
      all(r["role"] == "hidden_preset" for r in json.loads(get("/api/forms/usage?role=hidden_preset"))["rows"])
      and {r["field_id"] for r in json.loads(get("/api/forms/usage?field_id=%s" % EMAIL))["rows"]} == {EMAIL}
      and {r["field_name"] for r in json.loads(get("/api/forms/usage?q=refer"))["rows"]} == {"Referred By"})
rows = list(csv.DictReader(io.StringIO(get("/api/export/form_usage.csv"))))
check("usage csv header and rows",
      list(rows[0]) == ["base_id", "form_id", "form_name", "version_no", "field_id", "field_name", "table_name", "data_type", "role", "item_path", "detail",
                        "workfront_dev_field_id", "workfront_prod_field_id"] and len(rows) == len(u["rows"]), len(rows))
lk = list(csv.DictReader(io.StringIO(get("/api/export/form_links.csv"))))
check("links csv: path, visible text, address",
      [(x["path"], x["link_text"], x["url"]) for x in lk] == [("form", "Policy", "https://example.com/p?x=1"), ("1", "Brand", WIKI), ("2.2", "Email us", "mailto:help@example.com")], lk)

# ---------- reports ----------
src = get("/api/forms/report.html?form_id=viwCust0000002")
pos = [src.find(x) for x in ["Contact", "Full name", "Referred By", "Details", "Follow-up", "Email us"]]
check("source report: items appear in form order", all(p >= 0 for p in pos) and pos == sorted(pos), pos)
check("source report: link text with its real address printed beside it",
      'Brand</a> <span class="url">(%s)</span>' % WIKI in src and "mailto:help@example.com" in src)
check("source report: hidden field, logic, skip rule, bold and italic",
      "Hidden / preset field" in src and "(hidden field)" in src and "Skip rule" in src and "the end of the form" in src
      and "<strong>our brand</strong>" in src and "<em>legal</em>" in src)
check("report escapes markup typed into text (no live script)", "<script>alert(1)" not in src and "&lt;script&gt;alert(1)" in src)
check("report is a standalone page with version history and sign-off", "Version history" in src and "6. Review" in src and "Airtable (source)" in src)
src1 = get("/api/forms/report.html?form_id=viwCust0000002&version=1")
check("older version report shows the older label", "Your name" in src1 and "Full name" not in src1 and "(this report)" in src1)
tg = get("/api/forms/report.html?form_id=viwCust0000002&target=DEV")
check("target report with no crosswalk says not ready and flags UNMAPPED", "Not ready to copy" in tg and "UNMAPPED: Name" in tg and "Source: Airtable field" in tg, tg[:300])
c.execute("insert into workfront_fields values('DEV|wfName','DEV','WS','Contact',null,'wfName','Contact Name','Single Line Text',current_timestamp)")
c.execute("insert into workfront_fields values('DEV|wfRef','DEV','WS','Contact',null,'wfRef','Referral Source','Dropdown (Single Choice)',current_timestamp)")
c.execute("insert into workfront_fields values('DEV|wfAct','DEV','WS','Contact',null,'wfAct','Is Active','Boolean / Checkbox',current_timestamp)")
c.execute("insert into workfront_fields values('DEV|wfMail','DEV','WS','Contact',null,'wfMail','Email Address','Email',current_timestamp)")
for a, w, n in [(NAME, "wfName", "Name"), (REF, "wfRef", "Referred By"), (ACTIVE, "wfAct", "Active"), (EMAIL, "wfMail", "Email")]:
    c.execute("insert into field_crosswalk(airtable_base_id,airtable_table_name,airtable_field_id,airtable_field_name,workfront_dev_field_id) "
              "values('appSampleBase001','Customers',?,?,?)", (a, n, w))
c.commit()
tg = get("/api/forms/report.html?form_id=viwCust0000002&target=DEV")
check("target report: every field mapped, target names used in place of source",
      "Every field on this form is mapped" in tg and "UNMAPPED" not in tg and "Contact Name" in tg and "Referral Source" in tg and "Email Address" in tg, tg[:400])
check("target report: logic uses target fields; links and text carried over",
      "<b>Is Active</b>" in tg and "<b>Referral Source</b>" in tg and "(hidden field)" in tg and WIKI in tg and "mailto:help@example.com" in tg)
stt = json.loads(get("/api/forms/report/status?form_id=viwCust0000002"))
check("status counts: DEV fully mapped, PROD not", stt["unmapped"] == {"dev": 0, "prod": 4}, stt)
ur = json.loads(get("/api/forms/usage?field_id=%s" % NAME))["rows"]
check("usage rows carry the crosswalk's Workfront field ids", ur and all(r["workfront_dev_field_id"] == "wfName" and r["workfront_prod_field_id"] == "" for r in ur), ur)
stx, hx, bx = get("/api/forms/report.html?form_id=viwCust0000002&download=1", raw=True)
check("html download has a descriptive file name",
      re.match(r'attachment; filename="Form_viwCust0000002_v2_Airtable_\d{8}-\d{4}\.html"', hx.get("Content-Disposition", "")), hx)
stm, hm, bm = get("/api/forms/report.mhtml?form_id=viwCust0000002&target=PROD", raw=True)
check("mhtml download", stm == 200 and "_PROD_" in hm.get("Content-Disposition", "") and bm.startswith(b"From:") and b"multipart/related" in bm, hm)
st, _h, _b = get("/api/forms/report.html?form_id=viwCust0000002&target=BAD", raw=True)
check("bad target rejected", st == 400, st)
check("no token in any form response", "patMOCK" not in src + tg + get("/api/forms/list"))


# ---------- a form with no Airtable view ----------
def free(key, base="appSampleBase001"):
    return {"form": {"form_id": key, "form_name": "Free form", "view_id": "", "base_id": base, "table_id": "", "description_md": ""},
            "items": [{"id": "a1", "type": "field", "field_id": "fldOrdTotal0001", "label": "", "required": False, "help_md": "", "display_logic": OFF},
                      {"id": "a2", "type": "field", "field_id": NAME, "label": "", "required": False, "help_md": "", "display_logic": OFF}]}


r = post("/api/forms/save", free("form-free"))
check("form with no view: fields from any table in the base", r.get("version_no") == 1, r)
r = post("/api/forms/save", free("form-free", base="appSampleBase002"))
check("form key cannot be reused by another base", "error" in r and "already used" in r["error"], r)
r = post("/api/forms/save", {"form": {"form_id": "empty", "form_name": "Empty", "base_id": "appSampleBase001"}, "items": []})
check("an empty form can be saved", r.get("version_no") == 1, r)
fl = json.loads(get("/api/forms/list"))["forms"]
check("list now has three forms", len(fl) == 3, fl)

# ---------- request rules ----------
st, _h, _b = get("/api/forms/save", raw=True)
check("save is POST only", st == 404, st)
req = urllib.request.Request(B + "/api/forms/save", method="POST", data=b"{}", headers={"Content-Type": "text/plain"})
try:
    urllib.request.urlopen(req)
    code = 200
except urllib.error.HTTPError as e:
    code = e.code
check("save requires JSON", code == 415, code)
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
