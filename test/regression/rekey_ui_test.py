"""Browser test of the re-key page (/rekey): drives the real page in Chromium against a FAKE Workfront served from this script. Fabricated tables and records, a fake token. Checks that
nothing is read or changed when the page opens, that the check only reads, that re-keying stays closed until the initials, the reason and the exact phrase are given, that the
confirmation names the Workspace and Cancel changes nothing, the on-screen result and the report link, that the token never reaches the page, and that every scrolling window takes
keyboard focus."""
import json, os, sqlite3, sys, time, urllib.request
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    raise SystemExit("SKIP playwright is not installed (pip install playwright; playwright install chromium)")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import loadtable_fixture as fx
from loadtable_fixture import fake, BASE, TOKEN, DBF
B = "http://127.0.0.1:8765"
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:400])
def api(method, p, b=None):
    req = urllib.request.Request(B + p, method=method, data=None if b is None else json.dumps(b).encode(), headers={"Content-Type": "application/json"} if method == "POST" else {})
    return json.loads(urllib.request.urlopen(req).read())

srv = fx.start()
fx.build()
api("POST", "/api/db/connect", {"path": DBF})
orig = fx.set_config()
try:
    api("POST", "/api/emergency/start", {"base_id": BASE, "table_id": "tC", "confirm": {"table_id": "tC", "workspace_id": "wsDev"}})
    for _ in range(300):
        j = api("GET", "/api/emergency/status")["job"]
        if j and j["status"] != "running": break
        time.sleep(0.1)
    dbc = sqlite3.connect(DBF); dbc.execute("UPDATE airtable_tables SET primary_field_id='fC1' WHERE table_id='tC'"); dbc.commit(); dbc.close()
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page(viewport={"width": 1100, "height": 1000})
        reqs, errors, dialogs = [], [], []
        pg.on("request", lambda r: reqs.append((r.method, r.url)))
        pg.on("pageerror", lambda e: errors.append(str(e)))
        answer = {"accept": False}
        def on_dialog(d):
            dialogs.append(d.message)
            d.accept() if answer["accept"] else d.dismiss()
        pg.on("dialog", on_dialog)
        runs = lambda: [u for m, u in reqs if m == "POST" and "/api/rekey/run" in u]
        n_start = len(fake.reqs)

        pg.goto(B + "/rekey")
        pg.wait_for_function("document.getElementById('dbBadge').textContent.includes('connected')")
        pg.wait_for_function("document.getElementById('wsId').textContent.trim() === 'wsDev'")
        check("the page is in the left navigation and is the current page", "Re-key record types" in pg.eval_on_selector("#sidenav a.current", "e => e.textContent"))
        check("opening the page sent nothing to Workfront", len(fake.reqs) == n_start and not runs())
        check("the dev Workspace ID is shown read-only and there is no token on the page", pg.eval_on_selector("#wsId", "e => e.tagName") == "OUTPUT" and TOKEN not in pg.content())
        pg.fill("#baseId", "")
        pg.wait_for_function("document.getElementById('btnCheck').disabled")
        check("Check stays closed until a base is chosen", pg.is_disabled("#btnCheck"))
        pg.fill("#baseId", BASE)
        pg.wait_for_function("!document.getElementById('tableCard').hidden")
        check("the record types of the base are listed before anything is read from Planning", "Notes" in pg.inner_text("#rowGrid") and "not checked yet" in pg.inner_text("#rowGrid") and len(fake.reqs) == n_start)
        check("re-keying is closed: nothing has been checked", pg.is_hidden("#decideCard"))
        pg.click("#ctxCard h2")                       # close the base list that typing opened
        pg.click("#btnCheck")
        pg.wait_for_function("document.getElementById('rowGrid').textContent.includes('can be re-keyed')", timeout=30000)
        new = fake.reqs[n_start:]
        check("the check only reads Planning", new and all(m == "GET" for m, _p, _b in new), new)
        check("the record type is ticked, and its Airtable primary field and the current primary field are shown", pg.is_checked("#rowGrid input.pick") and "Note Text" in pg.inner_text("#rowGrid") and "AirtableRecordId" in pg.inner_text("#rowGrid"), pg.inner_text("#rowGrid"))
        check("the decision card appears, but Re-key is closed", pg.is_visible("#decideCard") and pg.is_disabled("#btnRekey"))
        pg.fill("#actor", "JV")
        check("it stays closed with only initials", pg.is_disabled("#btnRekey"))
        pg.fill("#reason", "data validated")
        pg.fill("#phrase", "rekey")
        check("it stays closed with the wrong phrase", pg.is_disabled("#btnRekey"))
        pg.fill("#phrase", "REKEY RECORD TYPES")
        check("it opens with initials, a reason and the exact phrase", pg.is_enabled("#btnRekey"))
        pg.uncheck("#rowGrid input.pick")
        check("it closes again when no record type is chosen", pg.is_disabled("#btnRekey"))
        pg.check("#rowGrid input.pick")
        n_before = len(fake.reqs)
        pg.click("#btnRekey"); pg.wait_for_timeout(400)
        check("the confirmation names the Workspace and the initials, and Cancel changes nothing", len(dialogs) == 1 and "wsDev" in dialogs[0] and "JV" in dialogs[0] and not runs() and len(fake.reqs) == n_before, dialogs)
        answer["accept"] = True
        pg.click("#btnRekey")
        pg.wait_for_function("!document.getElementById('resultCard').hidden && document.getElementById('resultMsg').textContent.includes('re-keyed')", timeout=30000)
        check("the result says how many were re-keyed and links to the report", "1 record type(s) re-keyed" in pg.inner_text("#resultMsg") and pg.eval_on_selector("#files a", "a => a.getAttribute('href')").startswith("/api/rekey/report?file="), pg.inner_text("#resultMsg"))
        check("the result row says rekeyed", "rekeyed" in pg.inner_text("#resultGrid"))
        check("the table now shows the record type as done", "already done" in pg.inner_text("#rowGrid"), pg.inner_text("#rowGrid"))
        check("no token on the page", TOKEN not in pg.content())
        wins = pg.eval_on_selector_all(".tablewin", "ws => ws.map(w => w.tabIndex)")
        check("every scrolling window takes keyboard focus", wins and all(t >= 0 for t in wins), wins)
        check("no script error on the page", errors == [], errors)
        br.close()
finally:
    open("config.json", "w", encoding="utf-8").write(orig)
    srv.shutdown()
sys.exit(0 if ok else 1)
