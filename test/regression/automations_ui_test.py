import json, urllib.request
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    raise SystemExit("SKIP playwright is not installed (pip install playwright; playwright install chromium)")
B = "http://127.0.0.1:8765"
ok = True


def check(n, c, x=""):
    global ok
    ok &= bool(c)
    print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:300])


def post(p, b):
    req = urllib.request.Request(B + p, method="POST", data=json.dumps(b).encode(), headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())


post("/api/db/create", {"path": "db/migration.db"})
post("/api/schema/ingest", {"base_id": "appSampleBase001", "source": "api"})


def pick(scope, text, option=None):
    """Use a type-ahead picker inside a locator: click, type, choose the first option containing text."""
    inp = scope.locator(".combo.pick input").first
    inp.click()
    inp.fill(text)
    scope.locator(".combo-menu li[role=option]", has_text=option or text).first.click()


with sync_playwright() as p:
    br = p.chromium.launch()
    ctx = br.new_context()
    pg = ctx.new_page()
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    dialogs = []
    pg.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
    pg.goto(B + "/stage3")
    pg.wait_for_timeout(1200)
    pg.fill("#baseId", "appSampleBase001")
    pg.dispatch_event("#baseId", "input")
    pg.fill("#newName", "Order follow-up")
    pg.click("#btnNew")
    pg.wait_for_timeout(400)
    check("starting an automation opens the editor with a name-based key",
          pg.is_visible("#editorCard") and pg.input_value("#autoKey") == "auto-order-follow-up" and pg.input_value("#autoName") == "Order follow-up", pg.input_value("#autoKey"))
    pg.fill("#scenarioName", "Fusion: Order follow-up")

    # ---- trigger ----
    tb = pg.locator("#triggerBox")
    tb.locator(".row", has=pg.locator(".lbl-t", has_text="Trigger")).locator("select").select_option("record_matches")
    tb.locator(".row", has=pg.locator(".lbl-t", has_text="Table")).locator("select").select_option("tblOrders000001")
    pick(tb.locator(".row", has=pg.locator(".lbl-t", has_text="Watches")), "Total", "Total")
    check("watched field shows as a chip", "Total" in tb.locator(".chips").inner_text(), tb.locator(".chips").inner_text())
    root = tb.locator(".logicbox > .cgroup").first
    root.locator(":scope > .row button", has_text="+ Condition").click()
    cr = tb.locator(".logicbox > .cgroup > .row.cond").first
    pick(cr, "Total", "Total")
    cr = tb.locator(".logicbox > .cgroup > .row.cond").first
    cr.locator("select").nth(1).select_option("greaterThan")
    cr.locator("input[placeholder=Value]").fill("100")
    tb.locator(".logicbox > .cgroup > .row button", has_text="+ Group").first.click()
    inner = tb.locator(".logicbox > .cgroup > .cgroup").first
    inner.locator(":scope > .row select").first.select_option("OR")
    inner.locator(":scope > .row button", has_text="+ Condition").click()
    ic = inner.locator(":scope > .row.cond").first
    ic.locator("select").first.select_option("text")
    inner.locator(":scope > .row.cond").first.locator("input[placeholder*='Today']").fill("Today")
    inner.locator(":scope > .row.cond").first.locator("select").nth(0).select_option("isAfter") if False else None
    check("trigger has a nested group inside the condition group", tb.locator(".cgroup .cgroup").count() == 1)

    # ---- steps ----
    add = "#rootAdd select.addsel"
    pg.select_option(add, "action:find_records")
    s1 = pg.locator("#tree > details.fitem").nth(0)
    s1.locator(".ibody > label.lbl input.wide").first.fill("Find the customer")
    s1.locator(".row", has=pg.locator(".lbl-t", has_text="Table")).locator("select").select_option("tblCustomers0001")
    s1.locator(".logicbox.display .cgroup > .row button", has_text="+ Condition").click()
    fc = s1.locator(".logicbox.display .cgroup > .row.cond").first
    check("find conditions list only the searched table's fields (no record source)",
          fc.locator("select").count() == 3 or fc.locator("select").count() == 2, fc.locator("select").count())
    pick(fc, "Name", "Name")
    check("find step offers no 'record from' choice", "record from" not in s1.locator(".logicbox.display").inner_text())

    pg.select_option(add, "conditional")
    s2 = pg.locator("#tree > details.fitem").nth(1)
    s2.locator(".ibody > label.lbl input.wide").first.fill("Route by status")
    b1 = s2.locator(".branchbox").first
    b1.locator(".logicbox.display .cgroup > .row button", has_text="+ Condition").click()
    bc = b1.locator(".logicbox.display .cgroup > .row.cond").first
    SRC = "select[title='Which record the field is read from']"
    src_opts = bc.locator(SRC).inner_text()
    check("a branch condition can read the record found by the earlier step", "Step 1: Find the customer" in src_opts, src_opts)
    bc.locator(SRC).select_option(label="record from Step 1: Find the customer")
    bc = b1.locator(".logicbox.display .cgroup > .row.cond").first
    pick(bc, "Active", "Active")
    bc = b1.locator(".logicbox.display .cgroup > .row.cond").first
    bc.locator("input[placeholder=Value]").fill("checked")
    b1.locator("select.addsel").select_option("action:update_record")
    up = b1.locator(".kids details.fitem").first
    up.locator(".row", has=pg.locator(".lbl-t", has_text="Table")).locator("select").select_option("tblOrders000001")
    up.locator("button", has_text="+ Add a field").click()
    fr = up.locator(".row.cond").first
    pick(fr, "Total", "Total")
    up.locator(".row.cond").first.locator("input.wide").fill("0")
    s2.locator("button", has_text="+ Else branch").click()
    check("else branch added and the add button disabled afterwards", s2.locator(".branchbox.else").count() == 1 and s2.locator("button:has-text('+ Else branch')").is_disabled())
    eb = s2.locator(".branchbox.else")
    eb.locator("select.addsel").select_option("action:send_email")
    em = eb.locator(".kids details.fitem").first
    check("an email step starts with To / Subject / Body details", all(x in em.inner_text() for x in ["To", "Subject", "Body"]))
    em.locator(".setting-row").nth(1).locator("textarea").first.fill("See [our terms](https://example.com/terms)")
    pg.wait_for_timeout(600)
    check("the detail's preview shows the link with its real address", "https://example.com/terms" in em.locator(".setting-row").nth(1).locator(".rich-preview").inner_text())

    pg.select_option(add, "repeat")
    s3 = pg.locator("#tree > details.fitem").nth(2)
    s3.locator(".ibody > label.lbl input.wide").first.fill("Each customer")
    s3.locator(".row", has=pg.locator(".lbl-t", has_text="Repeats over")).locator("select").select_option(label="records found in Step 1: Find the customer")
    s3.locator("select.addsel").first.select_option("action:run_script")
    sc = s3.locator(".kids details.fitem").first
    sc.locator("textarea.code").last.fill("if (a < b) { <b>x</b> }")

    # ---- move into previous (a step into a conditional's last branch), then out again ----
    pg.select_option(add, "action:delay")
    last = pg.locator("#tree > details.fitem").nth(3)
    last.locator(".ibody > label.lbl input.wide").first.fill("Pause")
    last.locator("> .ibody > .itemctl button", has_text="Into previous").click()
    check("'into previous' moves a step into the repeating group above it", pg.locator("#tree > details.fitem").count() == 3 and s3.locator(".kids details.fitem").count() == 2)
    s3.locator(".kids details.fitem").nth(1).locator("> .ibody > .itemctl button", has_text="Out").click()
    check("'out' moves it back after the group", pg.locator("#tree > details.fitem").count() == 4)
    pg.locator("#tree > details.fitem").nth(3).locator("> .ibody > .itemctl button", has_text="Remove").click()

    # ---- an invalid save is explained ----
    pg.click("#btnSave")
    pg.wait_for_timeout(900)
    check("saved as version 1", "Saved as version 1" in pg.inner_text("#saveMessage"), pg.inner_text("#saveMessage") + " | " + pg.inner_text("#saveProblems"))
    check("form key locked after the first save and the button names the version", pg.get_attribute("#autoKey", "readonly") is not None and "after v1" in pg.inner_text("#btnSave"))
    pg.click("#btnSave")
    pg.wait_for_timeout(500)
    check("saving again with no change makes no new version", "Nothing changed" in pg.inner_text("#saveMessage"), pg.inner_text("#saveMessage"))
    pg.select_option(add, "action:find_records")
    pg.locator("#tree > details.fitem").nth(3).locator(".ibody > label.lbl input.wide").first.fill("Needs a table")
    pg.locator("#tree > details.fitem").nth(3).locator(".row", has=pg.locator(".lbl-t", has_text="Table")).locator("select").select_option("")
    pg.click("#btnSave")
    pg.wait_for_timeout(700)
    check("a step without a table is rejected with a readable list", pg.is_visible("#saveProblems") and "needs a table" in pg.inner_text("#saveProblems"), pg.inner_text("#saveProblems"))
    pg.locator("#tree > details.fitem").nth(3).locator("> .ibody > .itemctl button", has_text="Remove").click()

    # ---- reports and usage ----
    pg.click("#btnRepPreview")
    pg.wait_for_timeout(900)
    body = pg.frame_locator("#repFrame").locator("body").inner_text()
    check("report preview shows the trigger first, then the steps in order",
          "Order follow-up" in body and body.find("Trigger") < body.find("Find the customer") < body.find("Route by status") < body.find("Each customer"), body[:200])
    check("report shows nested ANY/ALL logic and the link's address", "ANY of these" in body and "ALL of these" in body and "https://example.com/terms" in body)
    check("unmapped status is reported", "without a target mapping" in pg.inner_text("#repStatus"), pg.inner_text("#repStatus"))
    pg.select_option("#repTarget", "PROD")
    check("target choice updates the download links", "target=PROD" in pg.get_attribute("#lnkRepMhtml", "href"))
    pg.fill("#useQ", "Total")
    pg.click("#btnUsage")
    pg.wait_for_timeout(500)
    ut = pg.inner_text("#usageTable")
    check("usage shows Total as watched, tested in a condition and written", "watched by the trigger" in ut and "tested in a condition" in ut and "written (set)" in ut, ut)

    # ---- reopen (the page warns about unsaved changes, so save the clean state first) ----
    pg.click("#btnSave")
    pg.wait_for_timeout(500)
    pg.reload()
    pg.wait_for_timeout(1200)
    check("saved automations are listed after a reload", "Order follow-up" in pg.inner_text("#savedAutos"))
    pg.click("#btnOpen")
    pg.wait_for_timeout(800)
    check("opening restores the trigger, the nested logic and the steps",
          pg.input_value("#autoName") == "Order follow-up" and pg.input_value("#scenarioName") == "Fusion: Order follow-up"
          and pg.locator("#triggerBox .cgroup .cgroup").count() == 1 and pg.locator("#tree > details.fitem").count() == 3
          and pg.locator("#tree .branchbox").count() == 2, (pg.locator("#tree > details.fitem").count(), pg.locator("#tree .branchbox").count()))
    check("script text is shown exactly as saved (no markup injected)", "<b>x</b>" in pg.locator("textarea.code").last.input_value())
    check("no unsaved-changes prompt after a clean open", not dialogs, dialogs)
    real = [e for e in errs if "status of 400" not in e]
    check("no console errors (apart from the deliberate rejected save)", not real and len(errs) <= 1, errs)
    pg.screenshot(path="autos_ui.png", full_page=True)
    br.close()
print("ALL PASS" if ok else "SOME FAILED")
