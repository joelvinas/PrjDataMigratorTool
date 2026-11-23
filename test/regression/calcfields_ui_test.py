"""Browser test of the calculated fields page (/calcfields): drives the real page in Chromium against the real server and a FAKE Workfront served from this script. Fabricated tables,
fields and form names; a fake key. Checks: the page is in the navigation; the list of valid forms (a bad file shows its problems, a good one fills the form picker); Run preflight stays
locked until everything it needs is entered (with the reasons on hover); a run fills the counts and the grids; Approve needs initials and a reason; the read-only form check asks first;
the push asks first (declining sends nothing), then creates, uses an existing field, adds to the form and writes expressions, and the grid shows each item's step; the retry stays locked
when nothing failed; an earlier run opens again; every scrolling window takes keyboard focus; no key reaches the page."""
import json, os, sqlite3, sys, tempfile, urllib.request
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    raise SystemExit("SKIP playwright is not installed (pip install playwright; playwright install chromium)")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import calcfake as cfk
B = "http://127.0.0.1:8765"; BASE = "appCalcUi000001"; DBF = "calcfields_ui.db"; KEY = "fakeCOREkey0123456789abcdef"; FPORT = 9103
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:400])
def post(p, b):
    req = urllib.request.Request(B + p, method="POST", data=json.dumps(b).encode(), headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())

post("/api/db/create", {"path": DBF}); post("/api/db/connect", {"path": DBF})
c = sqlite3.connect(DBF)
def fld(fid, name, table, dtype="number", formula=None, rtype=None):
    c.execute("INSERT INTO airtable_fields (base_id, table_id, table_name, field_id, field_name, data_type, formula_text, result_type, is_valid) VALUES (?,?,?,?,?,?,?,?,1)", (BASE, table, "Special Orders", fid, name, dtype, formula, rtype))
fld("fldBase", "Base Price", "tblUi01")
fld("fldComm", "Commission", "tblUi01", "formula", "{fldBase}*0.15", "number")
fld("fldBad", "Months", "tblUi01", "formula", "DATETIME_DIFF({fldBase},{fldBase},'months')", "number")
fld("fldDepBad", "Reads Months", "tblUi01", "formula", "{fldBad}+1", "number")
fld("fldGhost", "Reads Ghost", "tblUi01", "formula", "{fldNothing}+1", "number")
for fid, nm in (("fldComm", "SalesPersonCommission"), ("fldBad", "MonthsOne"), ("fldDepBad", "ReadsMonths"), ("fldGhost", "ReadsGhost")):
    c.execute("INSERT INTO field_names (airtable_field_id, core_name, core_name_status) VALUES (?,?, 'approved')", (fid, nm))
c.execute("INSERT INTO core_field_crosswalk (environment, airtable_field_id, workfront_name, workfront_data_type, workfront_display_type, parameter_id) VALUES ('sandbox1', 'fldBase', 'base_price', 'NMBR', 'TEXT', 'P1')")
c.commit()

tmpd = tempfile.mkdtemp(prefix="cfdui_")
good, bad = os.path.join(tmpd, "forms.csv"), os.path.join(tmpd, "bad.csv")
open(good, "w", newline="").write("category_id,name\r\nCAT1,Special Order Sales\r\nCAT2,In Store Sales\r\n")
open(bad, "w", newline="").write("category_id,name\r\nCAT 1,Bad\r\n")
fake = cfk.FakeWorkfront(KEY)
fake.add_form("CAT1", "Special Order Sales", [{"parameterID": "X1", "displayOrder": 1, "isRequired": True, "securityLevel": "E", "parameterGroupID": "G1"}])
srv = cfk.start(fake, FPORT); orig_cfg = cfk.set_config(FPORT, KEY)
try:
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page(viewport={"width": 1300, "height": 1000})
        errors, requests, dialogs = [], [], []
        answer = {"accept": False}
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("request", lambda r: requests.append(r.url))
        def on_dialog(d):
            dialogs.append(d.message)
            d.accept() if answer["accept"] else d.dismiss()
        pg.on("dialog", on_dialog)
        pg.goto(B + "/calcfields")
        pg.wait_for_function("document.getElementById('dbBadge').textContent.includes('connected')")
        pg.wait_for_function("document.querySelectorAll('#tableSel option').length > 1")
        check("the page is in the left navigation and is the current page", "Calculated fields" in pg.eval_on_selector("#sidenav a.current", "e => e.textContent"))
        check("the table list shows the table with its formula count", "Special Orders" in pg.inner_text("#tableSel") and "4 formula fields" in pg.inner_text("#tableSel"), pg.inner_text("#tableSel"))
        check("the report, approval, push and earlier runs are hidden before a run", all(pg.is_hidden(s) for s in ("#reportCard", "#approveCard", "#pushCard", "#runsCard")))

        # ---- the list of valid forms
        check("with no list the form picker says to upload one, and Upload is locked", "Upload the list" in pg.inner_text("#formSel") and pg.is_disabled("#btnForms"))
        pg.set_input_files("#formsFile", bad); pg.click("#btnForms")
        pg.wait_for_selector("#formsProblemWin:not([hidden])")
        rows = pg.eval_on_selector_all("#formsProblemGrid tbody tr", "rs => rs.map(r => Array.from(r.cells).map(c => c.textContent))")
        check("a bad list shows its problems and loads nothing", len(rows) == 1 and rows[0][1] == "category_id" and pg.is_hidden("#formsWin") and "not loaded" in pg.inner_text("#formsMessage"), rows)
        pg.set_input_files("#formsFile", good); pg.click("#btnForms")
        pg.wait_for_selector("#formsWin:not([hidden])")
        check("a good list is shown and fills the form picker", pg.eval_on_selector_all("#formsGrid tbody tr", "r => r.length") == 2 and "Special Order Sales (CAT1)" in pg.inner_text("#formSel") and "2 valid form(s)" in pg.inner_text("#formsNote"))
        check("the template link is offered", pg.get_attribute("a[href='/api/calcfields/forms/template.csv']", "download") is not None)

        # ---- the preflight
        check("Run preflight is locked, and hovering says what is missing", pg.is_disabled("#btnRun") and "environment" in pg.get_attribute("#btnRun", "title"), pg.get_attribute("#btnRun", "title"))
        pg.select_option("#envSel", "sandbox1"); pg.select_option("#tableSel", "tblUi01")
        pg.wait_for_selector("#runsCard:not([hidden])")
        pg.fill("#actor", "JV")
        check("Run stays locked until a form is chosen from the list", pg.is_disabled("#btnRun") and "Choose a custom form" in pg.get_attribute("#btnRun", "title"))
        pg.select_option("#formSel", "CAT1")
        check("with everything chosen, Run is unlocked", not pg.is_disabled("#btnRun"))
        pg.click("#btnRun")
        pg.wait_for_selector("#reportCard:not([hidden])")
        check("the page says nothing was sent to Workfront, and the fake saw nothing", "Nothing was sent to Workfront" in pg.inner_text("#pageMessage") and fake.reqs == [], pg.inner_text("#pageMessage"))
        counts = pg.inner_text("#counts")
        check("the counts per status are shown", "4 formula field(s)" in counts and "Can be translated: 1" in counts and "Unsupported function: 1" in counts and "Depends on a blocked formula: 1" in counts and "Missing reference or Core name: 1" in counts, counts)
        ok_rows = pg.eval_on_selector_all("#okGrid tbody tr", "rs => rs.map(r => Array.from(r.cells).map(c => c.textContent))")
        check("the translatable grid shows the Airtable formula and the Workfront expression with the CALC displayType", len(ok_rows) == 1 and ok_rows[0][2] == "SalesPersonCommission" and ok_rows[0][3] == "NMBR" and ok_rows[0][4] == "CALC"
              and ok_rows[0][5] == "{fldBase}*0.15" and ok_rows[0][6] == "{DE:base_price}*0.15", ok_rows)
        check("each blocked status has its own grid and reason", "months" in pg.inner_text("#unsGrid") and "blocked" in pg.inner_text("#depGrid") and "fldNothing" in pg.inner_text("#missGrid"))
        check("the function inventory lists the functions found", "DATETIME_DIFF" in pg.inner_text("#fnGrid"))
        check("the CSV link points at this run", "/api/calcfields/report.csv?run_id=" in pg.get_attribute("#csvLink", "href"))
        check("the push section is not offered before approval", pg.is_hidden("#pushCard"))

        # ---- approval
        check("Approve is locked until a reason is given, with the reason on hover", pg.is_disabled("#btnApprove") and "reason" in pg.get_attribute("#approveWrap", "title"))
        pg.fill("#approveReason", "Reviewed every expression")
        pg.click("#btnApprove")
        pg.wait_for_function("document.getElementById('approveState').textContent.includes('Approved by JV')")
        check("approval shows who, when and why, and an approved run cannot be approved again", "Reviewed every expression" in pg.inner_text("#approveState") and pg.is_disabled("#btnApprove"))

        # ---- the form check and the push
        pg.wait_for_selector("#pushCard:not([hidden])")
        check("the push section appears with the push unlocked and the retry locked (nothing has failed)", not pg.is_disabled("#btnPush") and pg.is_disabled("#btnRetry") and "No item has failed" in pg.get_attribute("#retryWrap", "title"))
        check("production's typed phrase is not asked for in sandbox1", pg.is_hidden("#phraseBox"))
        answer["accept"] = False
        pg.click("#btnCheckForm")
        pg.wait_for_function("document.getElementById('formCheck').textContent.length > 0")
        check("declining the form check sends nothing", "Nothing was sent" in pg.inner_text("#formCheck") and fake.reqs == [] and any("only READS" in m for m in dialogs), (dialogs, fake.reqs))
        answer["accept"] = True
        pg.click("#btnCheckForm")
        pg.wait_for_function("document.getElementById('formCheck').textContent.includes('Found')")
        check("the form check reads one form and reports its name and size", "Special Order Sales" in pg.inner_text("#formCheck") and "1 field(s)" in pg.inner_text("#formCheck") and "matches the list" in pg.inner_text("#formCheck") and [m for m, pth, q, b in fake.reqs] == ["GET"], (pg.inner_text("#formCheck"), fake.reqs))
        existing = fake.add_param("salespersoncommission", "NMBR", "CALC")
        n0 = len(fake.reqs)
        answer["accept"] = False
        pg.click("#btnPush")
        pg.wait_for_function("document.getElementById('pushMsg').textContent.length > 0")
        check("declining the push sends nothing", "Nothing was sent" in pg.inner_text("#pushMsg") and len(fake.reqs) == n0 and any("CREATES fields" in m and "kept exactly as they are" in m for m in dialogs), dialogs)
        answer["accept"] = True
        pg.click("#btnPush")
        pg.wait_for_function("document.getElementById('pushMsg').textContent.includes('Push done')", timeout=15000)
        msg = pg.inner_text("#pushMsg")
        check("the push finishes and says what was done", "0 created" in msg and "1 existing field(s) used" in msg and "1 added to the form" in msg and "1 expression(s) written" in msg and "1 verified" in msg, msg)
        pg.wait_for_function("document.querySelector('#okGrid tbody tr').cells[8].textContent === 'verified'")
        check("the grid shows the item's step, and the existing field was used rather than created", not any((m, pth) == ("POST", "/param") for m, pth, q, b in fake.reqs) and existing in [a["parameterID"] for a in fake.forms["CAT1"]["assoc"]], fake.reqs)
        check("the form kept its first field's section", fake.forms["CAT1"]["assoc"][0].get("parameterGroupID") == "G1" and fake.forms["CAT1"]["assoc"][-1]["rawCustomExpression"] == "{DE:base_price}*0.15", fake.forms["CAT1"]["assoc"])
        check("the push is locked once everything is verified, and the run says it is pushed", pg.is_disabled("#btnPush") and "Everything in this run is already pushed" in pg.get_attribute("#pushWrap", "title") and "finished" in pg.inner_text("#approveState"))
        check("the earlier runs list shows the state", "pushed" in pg.inner_text("#runsGrid"), pg.inner_text("#runsGrid"))

        pg.reload()
        pg.wait_for_function("document.querySelectorAll('#tableSel option').length > 1")
        check("the list of valid forms survives a reload (it is held by the server)", "Special Order Sales (CAT1)" in pg.inner_text("#formSel"))
        pg.select_option("#tableSel", "tblUi01")
        pg.wait_for_function("document.querySelectorAll('#runsGrid tbody tr').length === 1")
        pg.click("#runsGrid tbody tr button")
        pg.wait_for_selector("#reportCard:not([hidden])")
        check("an earlier run opens again with its saved report and push step", "verified" in pg.inner_text("#okGrid") and "{DE:base_price}*0.15" in pg.inner_text("#okGrid"))

        wins = pg.eval_on_selector_all(".tablewin", "ws => ws.map(w => [w.getAttribute('tabindex'), w.getAttribute('role')])")
        check("every scrolling window takes keyboard focus", len(wins) >= 8 and all(w == ["0", "region"] for w in wins), wins)
        pg.focus("#okWin"); pg.keyboard.press("ArrowRight")
        check("a window holds the focus for the arrow keys", pg.evaluate("document.activeElement.id") == "okWin")
        check("the page only asked the local server", all(u.startswith(B) for u in requests), [u for u in requests if not u.startswith(B)])
        check("the key never reaches the page", KEY not in pg.content() and "bearer" not in pg.content().lower())
        check("no script error", errors == [], errors)
        br.close()
finally:
    srv.shutdown()
    open("config.json", "w", encoding="utf-8").write(orig_cfg)
c.close()
print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
