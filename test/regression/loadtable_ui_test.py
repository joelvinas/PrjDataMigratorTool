"""Browser test of the Load a table page (/loadtable): drives the real page in Chromium through every step against a FAKE Workfront served from this script. Fabricated tables and
records, a fake token. Checks that nothing runs when the page opens, the step gating, the confirmation dialog (it names the table, the stage and the Workspace ID, and Cancel sends
nothing), a failed step, a cancel and a rerun, the typed phrase for a reset, that the token never reaches the page, and that every scrolling window takes keyboard focus."""
import json, os, sys, time, urllib.request
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
        pg = br.new_page(viewport={"width": 1000, "height": 1100})
        reqs, errors = [], []
        pg.on("request", lambda r: reqs.append((r.method, r.url)))
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("dialog", lambda d: d.dismiss())
        def wait_text(sel, text, secs=25):
            pg.wait_for_function("([s, t]) => document.querySelector(s).textContent.includes(t)", arg=[sel, text], timeout=secs * 1000)
        def text(sel): return pg.inner_text(sel)
        def enabled(sel): return pg.is_enabled(sel)
        def api_posts(tail=""): return [u for m, u in reqs if m == "POST" and "/api/loadtable/" + tail in u]

        # ---- opening the page runs nothing
        pg.goto(B + "/loadtable")
        pg.wait_for_function("document.getElementById('dbBadge').textContent.includes('connected')")
        pg.wait_for_function("document.getElementById('wsId').textContent.trim() === 'wsDev'")
        check("the page is in the left navigation and marked as the current page", pg.eval_on_selector("#sidenav a.current", "e => e.textContent").startswith("Load a table"))
        check("the Workspace ID from config.json is shown, read-only, and the token is not on the page", pg.eval_on_selector("#wsId", "e => e.tagName") == "OUTPUT" and TOKEN not in pg.content())
        pg.fill("#baseId", BASE)
        pg.wait_for_function("document.getElementById('tableSel').options.length > 1")
        opts = pg.eval_on_selector_all("#tableSel option", "os => os.map(o => [o.value, o.textContent])")
        check("the picker lists the in-scope tables only (Hidden is absent) with the name-approval status", [o[0] for o in opts[1:]] == ["tB", "tC", "tA", "tE"] and all("name approved" in o[1] for o in opts[1:]), opts)
        pg.wait_for_function("document.getElementById('wsBadge').textContent.includes('Rehearsal Workspace')")
        check("the stages are the ones defined in config.json, in lower case", pg.eval_on_selector_all("#stageSel option", "os => os.map(o => o.value)") == ["dev", "uat", "production"])
        check("the Workspace name is fetched, not typed, and shown in a green third badge", "ok" in pg.eval_on_selector("#wsBadge", "e => e.className") and text("#wsBadge") == "Workspace: Rehearsal Workspace")
        check("opening the page and choosing a base started no step and sent nothing to Workfront except the one read of the Workspace", api_posts("start") == [] and fake.reqs and all(m == "GET" and "/v2/workspaces/wsDev" in u for m, u, _b in fake.reqs), fake.reqs)
        check("with no table chosen every step button is closed", all(not enabled(s) for s in ("#btnCheck", "#btnStructDry", "#btnStructSend", "#btnRecDry", "#btnRecSend", "#btnLinkDry", "#btnLinkSend", "#btnVerify", "#btnResetDry", "#btnResetSend")))

        # ---- a stage whose Workspace is not found keeps every step locked and says what to fix
        pg.select_option("#stageSel", "uat")
        pg.wait_for_function("document.getElementById('wsBadge').textContent.includes('not reachable')")
        pg.select_option("#tableSel", "tC")
        check("a Workspace Workfront does not know turns the badge red, names the config.json value to fix, and locks the steps", "bad" in pg.eval_on_selector("#wsBadge", "e => e.className") and "workspace_id" in text("#pageMessage") and "wsMissing" in text("#wsId")
              and not enabled("#btnStructDry") and not enabled("#btnStructSend"))
        fake.reject = True
        pg.select_option("#stageSel", "dev")
        pg.wait_for_function("document.getElementById('wsBadge').textContent.includes('not reachable')")
        check("an expired token says tokens last 24 hours and to paste a new bearer_token", "24 hours" in text("#pageMessage") and "bearer_token" in text("#pageMessage") and TOKEN not in pg.content())
        fake.reject = False
        pg.select_option("#stageSel", "dev")
        pg.wait_for_function("document.getElementById('wsBadge').textContent.includes('Rehearsal Workspace')")

        # ---- step 1: a blocked table stops everything
        pg.select_option("#tableSel", "tE")
        check("choosing a table shows its scope, target and name status, and still runs nothing", "scope: in" in text("#tableInfo") and "Planning only" in text("#tableInfo") and api_posts("start") == [] and enabled("#btnCheck"))
        pg.click("#btnCheck")
        wait_text("#checkMsg", "Blocked")
        check("a blocked table says so, lists the reasons, and opens nothing below", "Nothing below opens" in text("#checkMsg") and pg.is_visible("#reasonGrid") and "data type" in text("#reasonGrid") and not enabled("#btnStructDry") and "blocked" in text("#pill1"))

        # ---- the happy path for a table with no links (Notes)
        pg.select_option("#tableSel", "tC")
        check("changing the table clears what was shown for the old one", not pg.is_visible("#reasonGrid") and text("#checkMsg") == "")
        pg.click("#btnCheck")
        wait_text("#checkMsg", "Clear")
        check("step 1 for a clear table: counts, and only step 2 opens", "2 item(s) ready" in text("#checkMsg") and enabled("#btnStructDry") and not enabled("#btnStructSend") and not enabled("#btnFile") and not enabled("#btnRecDry"), text("#checkMsg"))
        n_fake = len(fake.reqs)
        pg.click("#btnStructDry")
        wait_text("#structMsg", "Dry run")
        check("the dry run lists what it would create and sends nothing; Send opens only now", "would create 2" in text("#structMsg") and "would create" in text("#structGrid") and len(fake.reqs) == n_fake and enabled("#btnStructSend"), text("#structMsg"))
        pg.click("#btnStructSend")
        pg.wait_for_selector("#confirmDlg[open]")
        check("the confirmation names the table, the stage and the Workspace ID", "Notes (tC)" in text("#confirmTable") and text("#confirmStage") == "DEV" and text("#confirmWs") == "wsDev (Rehearsal Workspace)", [text("#confirmTable"), text("#confirmStage"), text("#confirmWs")])
        pg.click("#confirmNo")
        pg.wait_for_selector("#confirmDlg:not([open])", state="attached")
        check("Cancel on the confirmation sends nothing and starts no job", len(fake.reqs) == n_fake and len(api_posts("start")) == 1)
        pg.click("#btnStructSend")
        pg.wait_for_selector("#confirmDlg[open]")
        pg.keyboard.press("Escape")
        pg.wait_for_selector("#confirmDlg:not([open])", state="attached")
        check("Escape on the confirmation also sends nothing", len(fake.reqs) == n_fake)
        pg.click("#btnStructSend"); pg.wait_for_selector("#confirmDlg[open]"); pg.click("#confirmYes")
        wait_text("#structMsg", "Done")
        check("a confirmed send creates the record type and the field", "created 2" in text("#structMsg") and len(fake.posts("/record-types")) == 1 and len(fake.posts("/fields")) == 1 and fake.bad_auth == 0, text("#structMsg"))
        pg.wait_for_function("document.getElementById('mapGrid').querySelectorAll('tbody tr').length === 2")
        mp = pg.eval_on_selector_all("#mapGrid tbody tr", "rs => rs.map(r => Array.from(r.cells).map(c => c.textContent))")
        check("the Workfront ID map shows the Airtable field, the new name and the Workfront ID", mp[0][0] == "record type" and mp[1][1] == "fC1" and mp[1][2] == "Note Text" and mp[1][3] == "Note Text" and mp[0][5].startswith("RT") and mp[1][5].startswith("FLD"), mp)
        check("step 2 is done and step 3 opens", "done" in text("#pill2") and enabled("#btnFile") and enabled("#btnRecDry") and not enabled("#btnRecSend"))
        pg.click("#btnFile")
        wait_text("#fileMsg", "Saved file found")
        check("the saved-file check shows counts only", "2 record(s)" in text("#fileMsg") and "SECRETVALUE" not in pg.content())
        pg.click("#btnRecDry")
        wait_text("#recMsg", "would send 2")
        check("the records dry run shows the counts, and Send opens", "2 record(s) in the saved file" in text("#recMsg") and enabled("#btnRecSend"), text("#recMsg"))
        pg.click("#btnRecSend"); pg.wait_for_selector("#confirmDlg[open]"); pg.click("#confirmYes")
        wait_text("#recMsg", "Done")
        check("the records are created; counts only; step 5 opens and step 4 says there is nothing to do", "2 created, 0 failed" in text("#recMsg") and "done" in text("#pill3") and "nothing to do" in text("#pill4") and enabled("#btnVerify") and not enabled("#btnLinkDry"), text("#recMsg"))
        pg.click("#btnVerify")
        wait_text("#verifyMsg", "agree")
        check("verify shows the five counts side by side and they agree", pg.eval_on_selector_all("#verifyGrid tbody tr", "rs => rs.length") == 5 and "All counts agree" in text("#verifyMsg"))

        # ---- a failed step on Orders, then the fix
        fake.refuse_fields = {"Order Name"}
        pg.select_option("#tableSel", "tA")
        pg.click("#btnCheck"); wait_text("#checkMsg", "Clear")
        check("Orders' step 1 names the table its link points to", pg.is_visible("#depGrid") and "Customers" in text("#depGrid") and "no" in text("#depGrid"), text("#depGrid"))
        pg.click("#btnStructDry"); wait_text("#structMsg", "Dry run")
        pg.click("#btnStructSend"); pg.wait_for_selector("#confirmDlg[open]"); pg.click("#confirmYes")
        wait_text("#structMsg", "failed 1")
        rows = pg.eval_on_selector_all("#structGrid tbody tr", "rs => rs.map(r => Array.from(r.cells).map(c => c.textContent))")
        check("a failed field is listed as failed with its reason, without the token; the dependency record type was created empty", any(r[2] == "Order Name" and r[3] == "failed" and TOKEN not in r[4] for r in rows) and "Empty record type" in text("#structMsg") and TOKEN not in pg.content(), rows)
        cls = pg.eval_on_selector_all("#structGrid tbody tr", "rs => rs.map(r => [r.cells[3].textContent, r.className])")
        want = {"failed": "out-fail", "created": "out-ok", "already created": "out-ok", "on hold": "out-hold"}
        check("result rows are coloured by outcome (failed red, created green, on hold yellow, waiting for its parent plain)", any(o == "failed" for o, _ in cls) and all(c == want.get(o, "") for o, c in cls), cls)
        check("a failed step keeps step 3 closed", "done" not in text("#pill2") and not enabled("#btnFile") and not enabled("#btnRecDry"))
        fake.refuse_fields = set()
        pg.click("#btnStructDry"); wait_text("#structMsg", "Dry run"); pg.click("#btnStructSend"); pg.wait_for_selector("#confirmDlg[open]"); pg.click("#confirmYes")
        wait_text("#structMsg", "Done")
        pg.wait_for_function("document.getElementById('pill2').textContent === 'done'")
        check("after the cause is fixed the rerun finishes the structure", "created 1" in text("#structMsg") and enabled("#btnRecDry"), text("#structMsg"))

        # ---- Customers first (the links need its records), then a cancel on Orders and a rerun
        pg.select_option("#tableSel", "tB")
        pg.click("#btnCheck"); wait_text("#checkMsg", "Clear")
        pg.click("#btnStructDry"); wait_text("#structMsg", "Dry run"); pg.click("#btnStructSend"); pg.wait_for_selector("#confirmDlg[open]"); pg.click("#confirmYes"); wait_text("#structMsg", "Done")
        pg.wait_for_function("document.getElementById('pill2').textContent === 'done'")
        pg.click("#btnRecDry"); wait_text("#recMsg", "Dry run"); pg.click("#btnRecSend"); pg.wait_for_selector("#confirmDlg[open]"); pg.click("#confirmYes"); wait_text("#recMsg", "Done")
        pg.select_option("#tableSel", "tA")
        pg.click("#btnCheck"); wait_text("#checkMsg", "Clear")
        pg.click("#btnRecDry"); wait_text("#recMsg", "Dry run")
        fake.delay = 0.6
        pg.click("#btnRecSend"); pg.wait_for_selector("#confirmDlg[open]"); pg.click("#confirmYes")
        wait_text("#jobProgress", "batch 1 of 3")
        check("the progress panel shows the table, the batch number and the counts, and a Cancel button", pg.is_visible("#jobPanel") and "Orders" in text("#jobProgress") and "created" in text("#jobProgress") and pg.is_visible("#btnCancel"), text("#jobProgress"))
        check("while a step runs, every step button is closed", all(not enabled(s) for s in ("#btnCheck", "#btnStructDry", "#btnRecDry", "#btnRecSend", "#btnVerify", "#btnResetDry")))
        pg.click("#btnCancel")
        wait_text("#recMsg", "Stopped")
        rtA = post("/api/loadtable/check", {"base_id": BASE, "table_id": "tA", "stage": "dev"})["steps"]["structure"]["record_type"]
        made = len(fake.store.get(rtA, []))
        check("a cancel stops between batches and says so; some records exist, not all", 0 < made < 5 and "Stopped between batches" in text("#recMsg"), (made, text("#recMsg")))
        fake.delay = 0
        pg.click("#btnRecDry"); wait_text("#recMsg", "Dry run")
        check("after a stop the dry run counts only what is left", "would send" in text("#recMsg") and "would send 5" not in text("#recMsg"), text("#recMsg"))
        pg.click("#btnRecSend"); pg.wait_for_selector("#confirmDlg[open]"); pg.click("#confirmYes")
        pg.wait_for_function("document.getElementById('pill3').textContent === 'done'")
        check("the rerun finishes the table and no record exists twice", len(fake.store[rtA]) == 5 and sum(len(v) for v in fake.store.values()) == 2 + 3 + 5, {k: len(v) for k, v in fake.store.items()})

        # ---- links
        check("step 4 is open for a table with links", enabled("#btnLinkDry") and not enabled("#btnLinkSend"))
        pg.click("#btnLinkDry"); wait_text("#linkMsg", "Dry run")
        check("the links dry run counts the records waiting", "5 record(s) are waiting" in text("#linkMsg"), text("#linkMsg"))
        pg.click("#btnLinkSend"); pg.wait_for_selector("#confirmDlg[open]"); pg.click("#confirmYes")
        wait_text("#linkMsg", "Done")
        check("the links pass reports updated, waiting and failed counts", "5 updated, 0 waiting" in text("#linkMsg") and "done" in text("#pill4"), text("#linkMsg"))

        # ---- the danger zone
        check("the reset is collapsed until opened", not pg.eval_on_selector("#step6", "e => e.open"))
        pg.click("#step6 summary")
        check("the reset's phrase box and delete button are closed until its dry run is done", not enabled("#resetPhrase") and not enabled("#btnResetSend") and enabled("#btnResetDry"))
        pg.click("#btnResetDry"); wait_text("#resetMsg", "Dry run")
        check("the reset dry run shows counts and the phrase to type", "5 mapped Planning record(s)" in text("#resetMsg") and text("#resetPhraseShown") == "DELETE RECORDS IN DEV" and enabled("#resetPhrase") and not enabled("#btnResetSend"), text("#resetMsg"))
        pg.fill("#resetPhrase", "delete records in dev")
        check("a wrong phrase keeps Delete closed", not enabled("#btnResetSend"))
        pg.fill("#resetPhrase", "DELETE RECORDS IN DEV")
        check("the exact phrase opens Delete", enabled("#btnResetSend"))
        n_before = sum(len(v) for v in fake.store.values())
        pg.click("#btnResetSend"); pg.wait_for_selector("#confirmDlg[open]")
        check("the reset confirmation names the table, the stage and the Workspace ID", "Orders (tA)" in text("#confirmTable") and text("#confirmWs").startswith("wsDev") and "cannot be undone" in text("#confirmText"))
        pg.click("#confirmYes"); wait_text("#resetMsg", "deleted")
        check("the reset deletes the table's records and says the memory was cleared", "5 record(s) deleted" in text("#resetMsg") and sum(len(v) for v in fake.store.values()) == n_before - 5 and "cleared" in text("#resetMsg"), text("#resetMsg"))
        pg.wait_for_function("document.getElementById('pill3').textContent !== 'done'")
        check("afterwards the table can be loaded again from step 3", enabled("#btnRecDry"))

        # ---- step 7: reset the structure; Workfront refuses first, then the person chooses to forget the IDs
        check("the structure reset is collapsed and its phrase, delete and forget buttons are closed until its dry run is done", not pg.eval_on_selector("#step7", "e => e.open"))
        pg.click("#step7 summary")
        check("only the dry run is open at first", enabled("#btnTblDry") and not enabled("#tblPhrase") and not enabled("#btnTblSend") and not pg.is_visible("#forgetRow"))
        pg.click("#btnTblDry"); wait_text("#tblMsg", "Dry run")
        check("the dry run shows the counts and the phrase to type", "recorded item(s)" in text("#tblMsg") and "would try to delete record type" in text("#tblMsg") and text("#tblPhraseShown") == "RESET TABLE IN DEV" and enabled("#tblPhrase") and not enabled("#btnTblSend"), text("#tblMsg"))
        pg.fill("#tblPhrase", "reset table in dev")
        check("a wrong phrase keeps Delete closed", not enabled("#btnTblSend"))
        pg.fill("#tblPhrase", "RESET TABLE IN DEV")
        check("the exact phrase opens Delete", enabled("#btnTblSend"))
        fake.rt_refuse = 403
        pg.click("#btnTblSend"); pg.wait_for_selector("#confirmDlg[open]")
        check("the confirmation names the record type delete, the table, the stage and the Workspace", "record type" in text("#confirmTitle").lower() and "Orders (tA)" in text("#confirmTable") and text("#confirmWs").startswith("wsDev") and "cannot be undone" in text("#confirmText"))
        pg.click("#confirmYes"); wait_text("#tblMsg", "did NOT delete")
        check("a refusal shows Workfront's reason, keeps the IDs and offers to forget them anyway", "403" in text("#tblMsg") and "RECORD_TYPE_IN_HIERARCHY" in text("#tblMsg") and pg.is_visible("#forgetRow") and enabled("#btnForget") and "done" in text("#pill2") and TOKEN not in text("#tblMsg"), text("#tblMsg"))
        pg.click("#btnForget"); pg.wait_for_selector("#confirmDlg[open]")
        check("the forget confirmation says nothing is sent to Workfront", "forget" in text("#confirmTitle").lower() and "Nothing is sent" in text("#confirmText"))
        pg.click("#confirmYes"); wait_text("#tblMsg", "Forgot")
        pg.wait_for_function("document.getElementById('pill2').textContent !== 'done'")
        check("after forgetting, the table starts over at step 2 and the forget choice goes away", enabled("#btnStructDry") and not enabled("#btnRecDry") and not pg.is_visible("#forgetRow") and "locked" in text("#pill7"), text("#tblMsg"))
        fake.rt_refuse = None

        # ---- the production stage asks for a typed phrase on top of the confirmation
        pg.select_option("#stageSel", "production")
        pg.wait_for_function("document.getElementById('wsBadge').textContent.includes('Live Workspace')")
        pg.select_option("#tableSel", "tC"); pg.click("#btnCheck"); wait_text("#checkMsg", "Clear")
        pg.click("#btnStructDry"); wait_text("#structMsg", "Dry run")
        n_fake = len(fake.reqs)
        pg.click("#btnStructSend"); pg.wait_for_selector("#confirmDlg[open]")
        check("the production confirmation shows the Workspace ID and name and needs the phrase typed before it can be confirmed", pg.is_visible("#confirmPhrase") and "wsProd (Live Workspace)" in text("#confirmWs") and text("#confirmStage") == "PRODUCTION" and not enabled("#confirmYes"))
        pg.fill("#confirmPhrase", "create in production")
        check("a wrong phrase keeps Confirm closed", not enabled("#confirmYes"))
        pg.fill("#confirmPhrase", "CREATE IN PRODUCTION")
        check("the exact phrase opens Confirm", enabled("#confirmYes"))
        pg.click("#confirmNo"); pg.wait_for_selector("#confirmDlg:not([open])", state="attached")
        check("cancelling sends nothing to production", len(fake.reqs) == n_fake)
        pg.select_option("#stageSel", "dev")
        pg.wait_for_function("document.getElementById('wsBadge').textContent.includes('Rehearsal Workspace')")

        # ---- keyboard, markup, and what must never be on the page
        check("every scrolling window on the page can take keyboard focus", pg.eval_on_selector_all(".tablewin", "els => els.length > 0 && els.every(e => e.getAttribute('tabindex') === '0' && e.getAttribute('role') === 'region' && !!e.getAttribute('aria-label'))"))
        pg.select_option("#tableSel", "tC"); pg.click("#btnCheck"); wait_text("#checkMsg", "Clear")
        pg.wait_for_function("document.getElementById('mapGrid').querySelectorAll('tbody tr').length > 0")
        pg.focus("#mapWin"); before = pg.eval_on_selector("#mapWin", "e => [e.scrollLeft, e.scrollWidth > e.clientWidth]")
        for k in ("ArrowRight", "ArrowRight", "ArrowRight"): pg.keyboard.press(k)
        pg.wait_for_timeout(400)
        after = pg.eval_on_selector("#mapWin", "e => e.scrollLeft")
        check("the arrow keys pan the ID map window", before[1] and after > before[0], (before, after))
        pg.click("#mapGrid thead th:nth-child(4)")
        check("a grid heading sorts the rows", pg.eval_on_selector("#mapGrid thead th:nth-child(4)", "e => e.getAttribute('aria-sort')") == "ascending")
        html = pg.content()
        store = pg.evaluate("JSON.stringify([Object.entries(sessionStorage), Object.entries(localStorage)])")
        check("the Workfront token is nowhere on the page or in the browser's storage", TOKEN not in html and TOKEN not in store and "SECRETVALUE" not in html and "SECRETVALUE" not in store)
        check("no script error was raised", errors == [], errors)
        br.close()
finally:
    open("config.json", "w", encoding="utf-8").write(orig)
    srv.shutdown()
sys.exit(0 if ok else 1)
