"""Browser test of the Core fields in bulk page (/corefields): drives the real page in Chromium against a FAKE Workfront Core served from this script. Fabricated fields, a fake key.
Checks that nothing is sent when the page opens, that a bad file shows its problems, that the scan pop-up gates the scan (declining sends nothing and keeps CREATE locked), the grid
and its tick boxes, that CREATE unlocks only after a scan and needs the person's name, that RESET stays locked until Confirm is ticked and a reason is given, the downloads, that the
key never reaches the page, and that every scrolling window takes keyboard focus."""
import os, sys, tempfile
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    raise SystemExit("SKIP playwright is not installed (pip install playwright; playwright install chromium)")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json, urllib.request
import corefields_fixture as fx
from corefields_fixture import fake, KEY, DBF
B = "http://127.0.0.1:8765"
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:400])
def post(p, b):
    req = urllib.request.Request(B + p, method="POST", data=json.dumps(b).encode(), headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())

HEAD = "field_id,name,label,dataType,displayType,description,isRequired\r\n"
GOOD = HEAD + "fldA1,Color Code,Color Code,TEXT,TEXT,A colour,true\r\nfldA1,Color Flag,Color Flag,TEXT,CHCK,,false\r\nfldB2,Budget Amount,Budget Amount,CURC,TEXT,,\r\n"
BAD = HEAD + "fldA1,Dup,,text,TEXT,,true\r\nfldA2,dup,,TEXT,SLCT,,true\r\n"
tmp = tempfile.mkdtemp(prefix="cfui_")
good, bad = os.path.join(tmp, "good.csv"), os.path.join(tmp, "bad.csv")
open(good, "w", newline="").write(GOOD); open(bad, "w", newline="").write(BAD)

srv = fx.start()
fx.build()
orig = fx.set_config()
post("/api/db/connect", {"path": DBF})
try:
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page(viewport={"width": 1300, "height": 1000})
        errors, dialogs = [], []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        answer = {"accept": False}
        def on_dialog(d):
            dialogs.append(d.message)
            d.accept() if answer["accept"] else d.dismiss()
        pg.on("dialog", on_dialog)

        pg.goto(B + "/corefields")
        pg.wait_for_function("document.getElementById('dbBadge').textContent.includes('connected')")
        pg.wait_for_function("document.querySelectorAll('#envSel option').length > 1")
        check("the page is in the left navigation and is the current page", "Core fields in bulk" in pg.eval_on_selector("#sidenav a.current", "e => e.textContent"))
        check("opening the page sent nothing to Workfront, and the grid is hidden", fake.reqs == [] and pg.is_hidden("#gridCard"))
        check("the environment list holds only the configured environments", pg.eval_on_selector_all("#envSel option", "o => o.map(x => x.value)") == ["", "sandbox1", "production"])
        check("Upload is disabled until a file is chosen", pg.is_disabled("#btnUpload"))

        pg.set_input_files("#csvFile", bad)
        pg.click("#btnUpload")
        pg.wait_for_selector("#problemWin:not([hidden])")
        rows = pg.eval_on_selector_all("#problemGrid tbody tr", "rs => rs.map(r => Array.from(r.cells).map(c => c.textContent))")
        check("a bad file lists every problem with its row and column and loads nothing", len(rows) == 3 and rows[0][1] == "dataType" and pg.is_hidden("#gridCard") and "not loaded" in pg.inner_text("#pageMessage"), rows)

        pg.select_option("#envSel", "sandbox1")
        pg.set_input_files("#csvFile", good)
        answer["accept"] = False
        pg.click("#btnUpload")
        pg.wait_for_selector("#gridCard:not([hidden])")
        pg.wait_for_function("document.getElementById('scanNote').textContent.length > 0")
        check("the pop-up asks before any scan, and declining sends nothing to Workfront", any("Scan Workfront" in m and "only READS" in m for m in dialogs) and fake.reqs == [], dialogs)
        names = pg.eval_on_selector_all("#fieldGrid tbody tr", "rs => rs.map(r => r.cells[3].textContent)")
        cells = pg.eval_on_selector("#fieldGrid tbody tr", "r => Array.from(r.cells).map(c => c.textContent)")
        check("the grid lists the file in order, with empty parameterId and lastUpdateDate", names == ["Color Code", "Color Flag", "Budget Amount"] and cells[9] == "" and cells[10] == "" and pg.locator("#fieldGrid tbody input:checked").count() == 3, (names, cells))
        check("CREATE is locked until a scan has completed, and the page says so", pg.is_disabled("#btnCreate") and "locked" in pg.inner_text("#scanNote"))
        tip = pg.get_attribute("#createWrap", "title")
        check("hovering the locked CREATE explains why: run CHECK FIELDS first, and a new CSV scans only its own fields", "CHECK FIELDS" in tip and "scans only those fields" in tip and "Enter your name" in tip, tip)
        check("RESET is red, carries a warning, and is locked with its reasons on hover", pg.is_disabled("#btnReset") and "destroy" in pg.get_attribute("#btnReset", "class") and "rgb(179, 38, 30)" == pg.eval_on_selector("#btnReset", "e => getComputedStyle(e).backgroundColor")
              and "⚠" in pg.inner_text("#btnReset") and "PERMANENTLY DELETES" in pg.inner_text("#resetWarn") and "CHECK FIELDS" in pg.get_attribute("#resetWrap", "title"), pg.get_attribute("#resetWrap", "title"))
        check("the confirmation cannot be ticked before a scan", pg.is_disabled("#resetConfirm"))

        fake.params["X1"] = {"ID": "X1", "name": "Color Flag", "lastUpdateDate": "2026-01-02 03:04:05"}
        answer["accept"] = True
        pg.click("#btnCheck")
        pg.wait_for_function("document.getElementById('resultMsg').textContent.includes('Scan in sandbox1: done')")
        cells = pg.eval_on_selector_all("#fieldGrid tbody tr", "rs => rs.map(r => Array.from(r.cells).map(c => c.textContent))")
        check("after the scan the existing field shows its parameterId and lastUpdateDate and is unticked", cells[1][9] == "X1" and cells[1][10] == "2026-01-02 03:04:05" and pg.locator("#fieldGrid tbody tr").nth(1).locator("input").is_checked() is False
              and pg.locator("#fieldGrid tbody tr").nth(1).locator("input").is_disabled(), cells)
        check("a scan report link appears", pg.locator("#downloads a", has_text="scan report").count() == 1)
        check("CREATE is still locked until a name is entered", pg.is_disabled("#btnCreate"))
        pg.fill("#actor", "J Tester")
        check("CREATE unlocks after the scan and a name, and its hover text is empty", pg.is_enabled("#btnCreate") and pg.get_attribute("#createWrap", "title") == "")

        n0 = len([r for r in fake.reqs if r[0] == "POST"])
        answer["accept"] = False
        pg.click("#btnCreate")
        check("declining the create pop-up sends nothing", len([r for r in fake.reqs if r[0] == "POST"]) == n0 and any("Create 2 field(s)" in m for m in dialogs), dialogs[-1])
        answer["accept"] = True
        pg.click("#btnCreate")
        pg.wait_for_function("document.getElementById('resultMsg').textContent.includes('Creation in sandbox1: done')")
        cells = pg.eval_on_selector_all("#fieldGrid tbody tr", "rs => rs.map(r => Array.from(r.cells).map(c => c.textContent))")
        check("created fields show their new parameterId and lastUpdateDate; the grid and the message agree", cells[0][9] == "P0001" and cells[0][10] and cells[2][9] == "P0002" and "2 of 2 field(s) created" in pg.inner_text("#resultMsg"), cells)
        check("the crosswalk CSV and the create report are offered", pg.locator("#downloads a", has_text="crosswalk").count() == 1 and pg.locator("#downloads a", has_text="create report").count() == 1)

        check("RESET stays locked until the confirmation is ticked AND a reason is typed", pg.is_disabled("#btnReset") and pg.is_enabled("#resetConfirm"))
        pg.check("#resetConfirm")
        check("the confirmation alone does not unlock RESET, and the hover says what is missing", pg.is_disabled("#btnReset") and "Give a reason" in pg.get_attribute("#resetWrap", "title"))
        pg.fill("#resetReason", "wrong dataType")
        check("confirmation plus a reason unlocks RESET", pg.is_enabled("#btnReset"))
        answer["accept"] = False
        pg.click("#btnReset")
        check("the delete pop-up warns it is permanent and lists the fields, including the one that already existed; declining deletes nothing", any("PERMANENT DELETION" in m and "Color Flag (X1)" in m and "already existed" in m for m in dialogs) and len(fake.params) == 3, dialogs[-1])
        answer["accept"] = True
        pg.click("#btnReset")
        pg.wait_for_function("document.getElementById('resultMsg').textContent.includes('Reset in sandbox1: done')")
        check("RESET deleted all three listed fields (the existing one too), and locked itself again", "3 of 3 field(s) deleted" in pg.inner_text("#resultMsg") and fake.params == {} and pg.is_disabled("#btnReset") and not pg.is_checked("#resetConfirm") and pg.is_disabled("#btnCreate"))

        pg.select_option("#envSel", "production")
        check("choosing production reveals the typed-phrase boxes and keeps CREATE locked", pg.is_visible("#createPhraseBox") and pg.is_visible("#resetPhraseBox") and pg.is_disabled("#btnCreate"))
        check("the key never reached the page", KEY not in pg.content())
        wins = pg.eval_on_selector_all(".tablewin", "ws => ws.map(w => w.tabIndex)")
        check("every scrolling window takes keyboard focus", wins and all(t >= 0 for t in wins), wins)
        check("no script error occurred", errors == [], errors)
        br.close()
finally:
    open("config.json", "w", encoding="utf-8").write(orig)
    srv.shutdown()
sys.exit(0 if ok else 1)
