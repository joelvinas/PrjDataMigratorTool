"""Browser test of the Emergency mode page (/emergency): drives the real page in Chromium against a FAKE Workfront served from this script. Fabricated tables and records, a fake
token. Checks that nothing runs when the page opens, that Go opens only with a base and a table, that the confirmation dialog names the table and the Workspace and Cancel sends
nothing, the on-screen result (counts, field rows, links to the reports), that the token never reaches the page, and that every scrolling window takes keyboard focus."""
import json, os, sys, urllib.request
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
def post(p, b):
    req = urllib.request.Request(B + p, method="POST", data=json.dumps(b).encode(), headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())

srv = fx.start()
fx.build()
post("/api/db/connect", {"path": DBF})
orig = fx.set_config()
try:
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
        starts = lambda: [u for m, u in reqs if m == "POST" and "/api/emergency/start" in u]

        pg.goto(B + "/emergency")
        pg.wait_for_function("document.getElementById('dbBadge').textContent.includes('connected')")
        pg.wait_for_function("document.getElementById('wsId').textContent.trim() === 'wsDev'")
        check("the page is in the left navigation with the siren and is the current page", "Git-R-Done" in pg.eval_on_selector("#sidenav a.current", "e => e.textContent") and "\U0001F6A8" in pg.eval_on_selector("#sidenav a.current", "e => e.textContent"))
        check("opening the page sent nothing to Workfront", fake.reqs == [] and not starts())
        check("the dev Workspace ID comes from config.json, shown read-only, with no token on the page", pg.eval_on_selector("#wsId", "e => e.tagName") == "OUTPUT" and TOKEN not in pg.content())
        check("there is no stage picker and no run-all: one Go button", pg.locator("#stageSel").count() == 0 and pg.locator("#btnGo").count() == 1 and pg.is_disabled("#btnGo"))
        pg.fill("#baseId", BASE)
        pg.wait_for_function("document.getElementById('tableSel').options.length > 1")
        opts = pg.eval_on_selector_all("#tableSel option", "os => os.map(o => o.value)")
        check("the picker lists the in-scope tables only (Hidden is absent)", opts[1:] == ["tB", "tC", "tA", "tE"], opts)
        check("Go stays closed until a table is chosen", pg.is_disabled("#btnGo"))
        pg.select_option("#tableSel", "tC")
        check("Go opens once a base and a table are chosen", pg.is_enabled("#btnGo"))
        pg.click("#btnGo")
        check("the confirmation names the table and the Workspace, and Cancel sends nothing", len(dialogs) == 1 and "Notes" in dialogs[0] and "tC" in dialogs[0] and "wsDev" in dialogs[0] and not starts() and fake.reqs == [], dialogs)

        answer["accept"] = True
        pg.click("#btnGo")
        pg.wait_for_function("!document.getElementById('resultCard').hidden && document.getElementById('countGrid').textContent.includes('Records loaded')", timeout=30000)
        rows = {r[0]: r[1] for r in pg.eval_on_selector_all("#countGrid tbody tr", "trs => trs.map(t => [t.cells[0].textContent, t.cells[1].textContent])")}
        check("the result shows the counts: clean, PRE- text, skipped, records loaded and failed", rows.get("Fields pushed clean") == "1" and rows.get("Fields converted to PRE- text") == "0" and rows.get("Fields skipped") == "0"
              and rows.get("Records loaded") == "2" and rows.get("Records failed") == "0", rows)
        links = pg.eval_on_selector_all("#files a", "as => as.map(a => a.getAttribute('href'))")
        check("links to the field report, the API log report and the CSV are shown", len(links) == 3 and all(l.startswith("/api/emergency/report?file=") for l in links) and any("_fields.mhtml" in l for l in links) and any("_apilog.mhtml" in l for l in links), links)
        check("the field row shows the Airtable name, the name sent and the outcome", "Note Text" in pg.inner_text("#fieldGrid") and "clean" in pg.inner_text("#fieldGrid"))
        check("no token and no record value on the page", TOKEN not in pg.content() and "SECRETVALUE" not in pg.content())
        wins = pg.eval_on_selector_all(".tablewin", "ws => ws.map(w => w.tabIndex)")
        check("every scrolling window takes keyboard focus", wins and all(t >= 0 for t in wins), wins)
        check("no script error on the page", errors == [], errors)
        br.close()
finally:
    open("config.json", "w", encoding="utf-8").write(orig)
    srv.shutdown()
sys.exit(0 if ok else 1)
