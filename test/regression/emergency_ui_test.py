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
        check("the stage picker lists the stages defined in config.json with dev chosen; no run-all: one Go button", pg.eval_on_selector_all("#stageSel option", "os => os.map(o => o.value)") == ["dev", "uat", "production"]
              and pg.input_value("#stageSel") == "dev" and pg.locator("#btnGo").count() == 1 and pg.is_disabled("#btnGo"))
        pg.select_option("#stageSel", "production")
        pg.wait_for_function("document.getElementById('wsId').textContent.trim() === 'wsProd'")
        check("choosing the production stage shows its Workspace ID and sends nothing", fake.reqs == [] and not starts())
        pg.select_option("#stageSel", "dev")
        pg.wait_for_function("document.getElementById('wsId').textContent.trim() === 'wsDev'")
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
        # ---- the links card (the second visit): hidden for a table with no link field, closed for a table that is not in Planning, a check that sends nothing, a confirmation for the real run
        check("a table with no link field shows no Links card", pg.is_hidden("#linksCard"))
        pg.select_option("#tableSel", "tA")
        pg.wait_for_function("!document.getElementById('linksCard').hidden")
        check("a table that is not in Planning yet says so and cannot be sent, but can be checked", "not in Planning yet" in pg.inner_text("#linksMsg") and pg.is_disabled("#btnLinks") and pg.is_enabled("#btnLinksCheck"), pg.inner_text("#linksMsg"))
        pg.select_option("#tableSel", "tB")
        pg.click("#btnGo")
        pg.wait_for_function("!document.getElementById('resultCard').hidden && document.getElementById('countGrid').textContent.includes('Records loaded')", timeout=30000)
        pg.wait_for_function("!document.getElementById('linksCard').hidden && !document.getElementById('btnLinks').disabled", timeout=15000)
        check("after Go the table's link field is listed and the links can be set", "Note Link" in pg.inner_text("#linksFieldGrid") and pg.is_enabled("#btnLinks"), pg.inner_text("#linksCard"))
        n_chk = len(fake.reqs); dialogs.clear()
        pg.click("#btnLinksCheck")
        pg.wait_for_function("!document.getElementById('linksResultCard').hidden", timeout=30000)
        check("the check asks nothing, sends nothing to Workfront and says so", dialogs == [] and len(fake.reqs) == n_chk and "nothing was sent" in pg.inner_text("#linksResultMsg") and pg.is_hidden("#resultCard"), (dialogs, pg.inner_text("#linksResultMsg")))
        check("the links result lists the counts", "Links set" in pg.inner_text("#linksCountGrid") and "Links waiting" in pg.inner_text("#linksCountGrid"))
        answer["accept"] = False; dialogs.clear()
        sends = lambda: [u for m, u in reqs if m == "POST" and "/api/emergency/links" in u]
        n_posts = len(sends())
        pg.click("#btnLinks"); pg.wait_for_timeout(400)
        check("the real links run asks first, names the table and the Workspace, and Cancel sends nothing", len(dialogs) == 1 and "Customers" in dialogs[0] and "wsDev" in dialogs[0] and len(sends()) == n_posts and len(fake.reqs) == n_chk, dialogs)
        answer["accept"] = True
        # ---- the picker: green when done, yellow when another pass is needed, and the new order (Customers links to Notes, which comes later, so it moves to just after it)
        pg.wait_for_function("document.querySelector('#tableSel option.again') !== null", timeout=15000)
        picks = pg.eval_on_selector_all("#tableSel option", "os => os.slice(1).map(o => [o.value, o.className, o.textContent])")
        check("the picker shows the new order: Notes, then Customers (it needs another pass after Notes), then the tables not run yet", [x[0] for x in picks] == ["tC", "tB", "tA", "tE"], picks)
        check("Notes is green (done), Customers is yellow (another pass needed), the others have no colour", [x[1] for x in picks] == ["done", "again", "", ""], picks)
        check("the picker numbers the new order and says why in words", picks[1][2].startswith("[#2] Customers") and picks[1][2].endswith("another pass needed") and picks[0][2].endswith("- done"), picks)
        check("the order panel is collapsed until it is opened", pg.eval_on_selector("#orderBox", "e => e.open") is False)
        pg.click("#orderBox summary")
        check("opened, it lists the tables in that order with their state, the table they wait for and why", "another pass needed" in pg.inner_text("#orderGrid") and "Notes" in pg.inner_text("#orderGrid") and "reference field" in pg.inner_text("#orderGrid"), pg.inner_text("#orderGrid"))
        pg.click("#orderBox summary")
        pg.select_option("#tableSel", "tC")
        # ---- reset: Workfront refuses first, then the person forgets the IDs; then Go again
        check("Reset table is open with a base and a table, and the forget choice is hidden", pg.is_enabled("#btnReset") and not pg.is_visible("#forgetRow"))
        n_before = len(fake.reqs)
        answer["accept"] = False; dialogs.clear()
        pg.click("#btnReset"); pg.wait_for_timeout(500)
        check("the reset confirmation shows the counts and the record type, and Cancel sends nothing to Workfront", len(dialogs) == 1 and "recorded item(s)" in dialogs[0] and "DELETE record type" in dialogs[0] and "wsDev" in dialogs[0] and len(fake.reqs) == n_before, dialogs)
        fake.rt_refuse = 403
        answer["accept"] = True; dialogs.clear()
        pg.click("#btnReset")
        pg.wait_for_function("document.getElementById('resetMsg').textContent.includes('did NOT delete')")
        check("a refusal shows Workfront's reason, keeps the IDs and offers to forget them anyway", "403" in pg.inner_text("#resetMsg") and "RECORD_TYPE_IN_HIERARCHY" in pg.inner_text("#resetMsg") and pg.is_visible("#forgetRow") and TOKEN not in pg.content(), pg.inner_text("#resetMsg"))
        n_f = len(fake.reqs); dialogs.clear()
        pg.click("#btnForget"); pg.wait_for_function("document.getElementById('resetMsg').textContent.includes('Forgot')")
        check("forgetting anyway asks first, sends nothing to Workfront, and hides the choice and the old result", "Nothing is sent" in dialogs[0] and len(fake.reqs) == n_f and not pg.is_visible("#forgetRow") and pg.is_hidden("#resultCard"), dialogs)
        fake.rt_refuse = None
        pg.click("#btnGo")
        pg.wait_for_function("!document.getElementById('resultCard').hidden && document.getElementById('countGrid').textContent.includes('Records loaded')", timeout=30000)
        check("Go builds the table again after the reset", pg.is_visible("#resultCard"))
        pg.click("#btnReset"); pg.wait_for_function("document.getElementById('resetMsg').textContent.includes('Record type deleted')")
        check("when Workfront deletes the record type the page says the IDs were forgotten and hides the old result", "Forgot" in pg.inner_text("#resetMsg") and pg.is_hidden("#resultCard") and not pg.is_visible("#forgetRow") and len(fake.rt_deleted) == 1, pg.inner_text("#resetMsg"))
        wins = pg.eval_on_selector_all(".tablewin", "ws => ws.map(w => w.tabIndex)")
        check("every scrolling window takes keyboard focus", wins and all(t >= 0 for t in wins), wins)
        check("no script error on the page", errors == [], errors)
        br.close()
finally:
    open("config.json", "w", encoding="utf-8").write(orig)
    srv.shutdown()
sys.exit(0 if ok else 1)
