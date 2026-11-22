"""Browser test of the calculated fields page (/calcfields): drives the real page in Chromium. Fabricated tables, fields and form names. Checks that the page is in the navigation, that
Run preflight stays locked until everything it needs is entered (with the reasons on hover), that a run fills the counts and the grids (expressions, blocked reasons kept apart),
that the page sends nothing to Workfront and no key reaches it, that Approve needs initials and a reason and then records the approval, that an earlier run can be opened again,
and that every scrolling window takes keyboard focus."""
import json, os, sqlite3, sys, urllib.request
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    raise SystemExit("SKIP playwright is not installed (pip install playwright; playwright install chromium)")
B = "http://127.0.0.1:8765"; BASE = "appCalcUi000001"; DBF = "calcfields_ui.db"
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

with sync_playwright() as p:
    br = p.chromium.launch()
    pg = br.new_page(viewport={"width": 1300, "height": 1000})
    errors, requests = [], []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("request", lambda r: requests.append(r.url))
    pg.goto(B + "/calcfields")
    pg.wait_for_function("document.getElementById('dbBadge').textContent.includes('connected')")
    pg.wait_for_function("document.querySelectorAll('#tableSel option').length > 1")
    check("the page is in the left navigation and is the current page", "Calculated fields" in pg.eval_on_selector("#sidenav a.current", "e => e.textContent"))
    check("the table list shows the table with its formula count", "Special Orders" in pg.inner_text("#tableSel") and "4 formula fields" in pg.inner_text("#tableSel"), pg.inner_text("#tableSel"))
    check("the report, approval and earlier runs are hidden before a run", pg.is_hidden("#reportCard") and pg.is_hidden("#approveCard") and pg.is_hidden("#runsCard"))
    check("Run preflight is locked, and hovering says what is missing", pg.is_disabled("#btnRun") and "environment" in pg.get_attribute("#btnRun", "title"), pg.get_attribute("#btnRun", "title"))
    pg.select_option("#envSel", "sandbox1")
    pg.select_option("#tableSel", "tblUi01")
    pg.wait_for_selector("#runsCard:not([hidden])")
    check("choosing a table shows its earlier runs (none yet)", pg.eval_on_selector_all("#runsGrid tbody tr", "r => r.length") == 0)
    pg.fill("#categoryId", "bad id!")
    check("a form ID with a space or symbol keeps Run locked", pg.is_disabled("#btnRun") and "letters and digits" in pg.get_attribute("#btnRun", "title"))
    pg.fill("#categoryId", "CAT1"); pg.fill("#categoryName", "Special Order Sales"); pg.fill("#actor", "JV")
    check("with everything entered, Run is unlocked", not pg.is_disabled("#btnRun"))

    pg.click("#btnRun")
    pg.wait_for_selector("#reportCard:not([hidden])")
    check("the page says nothing was sent to Workfront", "Nothing was sent to Workfront" in pg.inner_text("#pageMessage"), pg.inner_text("#pageMessage"))
    counts = pg.inner_text("#counts")
    check("the counts per status are shown", "4 formula field(s)" in counts and "Can be translated: 1" in counts and "Unsupported function: 1" in counts and "Depends on a blocked formula: 1" in counts and "Missing reference or Core name: 1" in counts, counts)
    ok_rows = pg.eval_on_selector_all("#okGrid tbody tr", "rs => rs.map(r => Array.from(r.cells).map(c => c.textContent))")
    check("the translatable grid shows the Airtable formula and the Workfront expression with the CALC displayType", len(ok_rows) == 1 and ok_rows[0][2] == "SalesPersonCommission" and ok_rows[0][3] == "NMBR" and ok_rows[0][4] == "CALC"
          and ok_rows[0][5] == "{fldBase}*0.15" and ok_rows[0][6] == "{DE:base_price}*0.15", ok_rows)
    check("each blocked status has its own grid and reason", "months" in pg.inner_text("#unsGrid") and "blocked" in pg.inner_text("#depGrid") and "fldNothing" in pg.inner_text("#missGrid"), (pg.inner_text("#unsGrid"), pg.inner_text("#depGrid"), pg.inner_text("#missGrid")))
    check("the function inventory lists the functions found", "DATETIME_DIFF" in pg.inner_text("#fnGrid"))
    check("the CSV link points at this run", "/api/calcfields/report.csv?run_id=" in pg.get_attribute("#csvLink", "href"))
    check("the earlier runs list now has this run", pg.eval_on_selector_all("#runsGrid tbody tr", "r => r.length") == 1)

    check("Approve is locked until a reason is given, with the reason on hover", pg.is_disabled("#btnApprove") and "reason" in pg.get_attribute("#approveWrap", "title"))
    pg.fill("#approveReason", "Reviewed every expression")
    check("with a reason (and initials) Approve is unlocked", not pg.is_disabled("#btnApprove"))
    pg.click("#btnApprove")
    pg.wait_for_function("document.getElementById('approveState').textContent.includes('Approved by JV')")
    check("approval shows who, when and why, and says nothing was sent", "Reviewed every expression" in pg.inner_text("#approveState") and "Nothing was sent to Workfront" in pg.inner_text("#approveMsg"))
    check("an approved run cannot be approved again", pg.is_disabled("#btnApprove"))
    check("the earlier runs list shows the state", "approved by JV" in pg.inner_text("#runsGrid"), pg.inner_text("#runsGrid"))

    pg.reload()
    pg.wait_for_function("document.querySelectorAll('#tableSel option').length > 1")
    pg.select_option("#tableSel", "tblUi01")
    pg.wait_for_function("document.querySelectorAll('#runsGrid tbody tr').length === 1")
    pg.click("#runsGrid tbody tr button")
    pg.wait_for_selector("#reportCard:not([hidden])")
    check("an earlier run opens again with its saved report", "Approved by JV" in pg.inner_text("#approveState") and "{DE:base_price}*0.15" in pg.inner_text("#okGrid"))

    wins = pg.eval_on_selector_all(".tablewin", "ws => ws.map(w => [w.getAttribute('tabindex'), w.getAttribute('role')])")
    check("every scrolling window takes keyboard focus", len(wins) >= 6 and all(w == ["0", "region"] for w in wins), wins)
    pg.focus("#okWin"); pg.keyboard.press("ArrowRight")
    check("a window holds the focus for the arrow keys", pg.evaluate("document.activeElement.id") == "okWin")

    check("the page asked for no Workfront route and no other host", not any("/api/corefields" in u or "/api/loadtable" in u for u in requests) and all(u.startswith(B) for u in requests), [u for u in requests if not u.startswith(B)])
    check("the key never reaches the page", "patMOCK" not in pg.content() and "bearer" not in pg.content().lower())
    check("no script error", errors == [], errors)
    br.close()
c.close()
print("OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
