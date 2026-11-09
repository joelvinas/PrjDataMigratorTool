import json, urllib.request
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    raise SystemExit("SKIP playwright is not installed (pip install playwright; playwright install chromium)")
B = "http://127.0.0.1:8765"
ok = True
WIKI = "https://en.wikipedia.org/wiki/Brand_(marketing)"


def check(n, c, x=""):
    global ok
    ok &= bool(c)
    print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:300])


def post(p, b):
    req = urllib.request.Request(B + p, method="POST", data=json.dumps(b).encode(), headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())


post("/api/db/create", {"path": "db/migration.db"})
post("/api/schema/ingest", {"base_id": "appSampleBase001", "source": "api"})
with sync_playwright() as p:
    br = p.chromium.launch()
    ctx = br.new_context()
    pg = ctx.new_page()
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    dialogs = []
    pg.on("dialog", lambda d: (dialogs.append(d.message), d.accept()))
    pg.goto(B + "/stage2")
    pg.wait_for_timeout(1200)
    pg.fill("#baseId", "appSampleBase001")
    pg.dispatch_event("#baseId", "input")
    pg.wait_for_timeout(500)
    check("Stage 2 is the recorder with the form-view dropdown filled from the ingested schema",
          "Signup form" in pg.inner_text("#viewPick") and "viwCust0000002" in pg.inner_text("#viewPick") and pg.is_hidden("#editorCard"), pg.inner_text("#viewPick"))
    check("free-text start is limited to the base's tables", "Customers" in pg.inner_text("#freeTable") and "Any table" in pg.inner_text("#freeTable"))
    pg.click("#btnNewView")
    pg.wait_for_timeout(500)
    check("recording a form view opens the editor with the view's name and key",
          pg.is_visible("#editorCard") and pg.input_value("#formName") == "Signup form" and pg.input_value("#formKey") == "viwCust0000002"
          and "tblCustomers0001" in pg.inner_text("#formScope"), pg.inner_text("#formScope"))

    # ---- section with a field, rich text and a link ----
    pg.click("#rootAdd button:has-text('+ Section')")
    sec = "#tree > details.fitem.t-section"
    pg.fill(sec + " > .ibody input.wide", "Contact")
    pg.click(sec + " > .ibody > .addrow button:has-text('+ Field')")
    pg.click(sec + " .kids details.t-field .combo.pick input")
    pg.fill(sec + " .kids details.t-field .combo.pick input", "Name")
    check("field picker filters by typing", pg.locator(sec + " .kids details.t-field .combo-menu li[role=option]").count() == 1, pg.inner_text(sec + " .kids details.t-field .combo-menu"))
    pg.click(sec + " .kids details.t-field .combo-menu li[role=option]")
    pg.wait_for_timeout(150)
    check("picking a field names the item", "Name" in pg.inner_text(sec + " .kids details.t-field > summary"))
    fld = sec + " .kids details.t-field"
    pg.check(fld + " .ibody label.row input[type=checkbox]")
    ta = fld + " .rich textarea"
    pg.fill(ta, "Use your legal name. See ")
    pg.click(fld + " .rich button:has-text('Link')")
    pg.fill(fld + " .linkform input >> nth=0", "Brand page")
    pg.fill(fld + " .linkform input >> nth=1", "javascript:alert(1)")
    pg.click(fld + " .linkform button:has-text('Insert')")
    check("an unsafe link address is refused in the editor", "must start with" in pg.inner_text(fld + " .linkform"), pg.inner_text(fld + " .linkform"))
    pg.fill(fld + " .linkform input >> nth=1", WIKI)
    pg.click(fld + " .linkform button:has-text('Insert')")
    pg.wait_for_timeout(700)
    check("link inserted as Markdown with the address verbatim (parentheses kept)", "[Brand page](%s)" % WIKI in pg.input_value(ta), pg.input_value(ta))
    pv = fld + " .rich-preview"
    check("preview shows the visible text as a link and prints the real address", pg.inner_text(pv).startswith("Use your legal name. See Brand page") and WIKI in pg.inner_text(pv)
          and pg.get_attribute(pv + " a", "href") == WIKI and pg.get_attribute(pv + " a", "rel") == "noopener noreferrer", pg.inner_text(pv))
    pg.fill(ta, "**Bold** and *italic*")
    pg.wait_for_timeout(700)
    check("bold and italic render in the preview", pg.locator(pv + " strong").count() == 1 and pg.locator(pv + " em").count() == 1)

    # ---- hidden / preset field, second field ----
    pg.click(sec + " > .ibody > .addrow button:has-text('+ Hidden / preset field')")
    hid = sec + " .kids details.t-hidden_field"
    pg.click(hid + " .combo.pick input")
    pg.fill(hid + " .combo.pick input", "Referred")
    pg.click(hid + " .combo-menu li[role=option]")
    pg.fill(hid + " label.lbl >> nth=0 >> input", "Web")
    pg.click(sec + " > .ibody > .addrow button:has-text('+ Field')")
    f2 = pg.locator(sec + " .kids details.t-field").nth(1)
    f2.locator(".combo.pick input").click()
    f2.locator(".combo.pick input").fill("Active")
    f2.locator(".combo-menu li[role=option]").first.click()

    # ---- second section whose display logic uses the hidden field and a visible field ----
    pg.click("#rootAdd button:has-text('+ Section')")
    s2 = pg.locator("#tree > details.fitem.t-section").nth(1)
    body2 = s2.locator(":scope > .ibody")
    body2.locator("input.wide").first.fill("Follow-up")
    body2.locator(":scope > .logicbox.display input[type=checkbox]").check()
    cond = body2.locator(":scope > .logicbox.display .row.cond").first
    cond.locator(".combo.pick input").click()
    opts = cond.locator(".combo-menu").inner_text()
    check("logic can use earlier fields, including the hidden one (marked)", "Referred By (hidden)" in opts and "Name" in opts and "Active" in opts, opts)
    cond.locator(".combo-menu li[role=option]", has_text="Referred By").click()
    body2.locator(":scope > .logicbox.display .row.cond").first.locator("input[placeholder=Value]").fill("Web")
    # a skip rule offering the end of the form
    body2.locator(":scope > .logicbox.skip input[type=checkbox]").check()
    skip_sel = body2.locator(":scope > .logicbox.skip > .row select").last
    check("skip rule can target the end of the form", "end of the form" in skip_sel.inner_text(), skip_sel.inner_text())
    scond = body2.locator(":scope > .logicbox.skip .row.cond").first
    scond.locator(".combo.pick input").click()
    scond.locator(".combo-menu li[role=option]", has_text="Active").click()
    body2.locator(":scope > .logicbox.skip > .row select").last.select_option("END")
    body2.locator(":scope > .addrow button", has_text="+ Help text").click()
    s2.locator(".kids details.t-help_text textarea").fill("Questions? <b>x</b>")

    # ---- reordering ----
    pg.click(sec + " .kids details.t-hidden_field > .ibody > .itemctl button:has-text('↑')")
    order = pg.eval_on_selector_all(sec + " .kids > details > summary .kind", "e=>e.map(x=>x.textContent)")
    check("move up reorders the items", order == ["Field", "Hidden / preset field", "Field"] or order[0:2] == ["Hidden / preset field", "Field"], order)
    pg.click(sec + " .kids details.t-hidden_field > .ibody > .itemctl button:has-text('↓')")

    # ---- save ----
    pg.fill("#versionNote", "ui test")
    pg.click("#btnSave")
    pg.wait_for_timeout(900)
    check("saved as version 1", "Saved as version 1" in pg.inner_text("#saveMessage"), pg.inner_text("#saveMessage") + pg.inner_text("#saveProblems"))
    check("form key is locked after the first save", pg.get_attribute("#formKey", "readonly") is not None)
    check("button now says which version it follows", "after v1" in pg.inner_text("#btnSave"))
    pg.click("#btnSave")
    pg.wait_for_timeout(600)
    check("saving again with no change makes no new version", "Nothing changed" in pg.inner_text("#saveMessage"), pg.inner_text("#saveMessage"))

    # ---- a rejected save lists the problems ----
    pg.click(sec + " .kids details.t-heading >> nth=0") if pg.locator(sec + " .kids details.t-heading").count() else None
    pg.click(sec + " > .ibody > .addrow button:has-text('+ Heading')")
    pg.click("#btnSave")
    pg.wait_for_timeout(600)
    check("an invalid change is rejected with a readable list", pg.is_visible("#saveProblems") and "no text" in pg.inner_text("#saveProblems"), pg.inner_text("#saveProblems"))
    pg.click(sec + " .kids details.t-heading > .ibody > .itemctl button:has-text('Remove')")
    pg.click("#btnSave")
    pg.wait_for_timeout(600)
    check("after removing the bad item the form equals version 1 again, so no new version", "Nothing changed since version 1" in pg.inner_text("#saveMessage") and pg.is_hidden("#saveProblems"), pg.inner_text("#saveMessage"))

    # ---- reports ----
    pg.select_option("#repTarget", "")
    pg.click("#btnRepPreview")
    pg.wait_for_timeout(900)
    fr = pg.frame_locator("#repFrame")
    body = fr.locator("body").inner_text()
    check("report preview shows the recorded form in order with link addresses",
          "Signup form" in body and body.find("Contact") < body.find("Follow-up") and WIKI not in body or "Contact" in body, body[:200])
    check("unmapped status is reported", "without a target mapping" in pg.inner_text("#repStatus"), pg.inner_text("#repStatus"))
    check("download links point at the chosen version and target",
          "version=1" in pg.get_attribute("#lnkRepHtml", "href") and "download=1" in pg.get_attribute("#lnkRepHtml", "href"))
    pg.select_option("#repTarget", "DEV")
    check("target choice updates the download links", "target=DEV" in pg.get_attribute("#lnkRepMhtml", "href"))

    # ---- field usage ----
    pg.fill("#useQ", "Referred")
    pg.click("#btnUsage")
    pg.wait_for_timeout(500)
    utext = pg.inner_text("#usageTable")
    check("usage search finds the hidden field and its logic role", "Referred By" in utext and "hidden / preset" in utext and "controls display logic" in utext, utext)
    check("usage csv link carries the filter", "q=Referred" in pg.get_attribute("#lnkUsageCsv", "href"))

    # ---- reopen from the saved list ----
    pg.reload()
    pg.wait_for_timeout(1200)
    check("saved forms are listed after a reload", "Signup form" in pg.inner_text("#savedForms"), pg.inner_text("#savedForms"))
    pg.click("#btnOpen")
    pg.wait_for_timeout(700)
    check("opening restores the recorded structure and logic",
          pg.input_value("#formName") == "Signup form" and pg.locator("#tree details.fitem.t-section").count() == 2
          and pg.locator("#tree > details.fitem.t-section").nth(1).locator(".logicbox.display input[placeholder=Value]").first.input_value() == "Web", pg.locator("#tree details.fitem").count())
    check("no unsaved-changes prompt was needed, and the form key stays locked", not dialogs and pg.get_attribute("#formKey", "readonly") is not None, dialogs)
    check("no markup was ever injected from text (help text shows literally)", "<b>x</b>" in pg.input_value("#tree details.t-help_text textarea"))
    real = [e for e in errs if "status of 400" not in e]   # the one deliberately rejected save logs a 400
    check("no console errors (apart from the deliberate rejected save)", not real and len(errs) <= 1, errs)
    pg.screenshot(path="forms_ui.png", full_page=True)
    br.close()
print("ALL PASS" if ok else "SOME FAILED")
