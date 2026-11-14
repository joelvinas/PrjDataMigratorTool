"""Browser test of the Field names page (/names): drives the real page in Chromium through the whole workflow. Fabricated names only."""
import json, os, shutil, sqlite3, time, urllib.request
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    raise SystemExit("SKIP playwright is not installed (pip install playwright; playwright install chromium)")
B = "http://127.0.0.1:8765"
BASE = "appNamesUi00001"
TA, TB, TC = "tblUa0000000001", "tblUb0000000001", "tblUc0000000001"
ok = True


def check(n, c, x=""):
    global ok
    ok &= bool(c)
    print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:300])


def post(p, b):
    req = urllib.request.Request(B + p, method="POST", data=json.dumps(b).encode(), headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())


def fld(i, n, t="singleLineText", o=None):
    d = {"id": "fld" + i, "name": n, "type": t}
    if o:
        d["options"] = o
    return d


HOSTILE = '<img src=x onerror="window.__xss=1">'
schema = {"tables": [
    {"id": TA, "name": "Orders", "primaryFieldId": "fldU01", "views": [], "fields": [
        fld("U01", "Order Name"), fld("U02", "Amount", "number"), fld("U03", "amount", "number"), fld("U04", "Notes!"), fld("U05", "Café ☕"),
        fld("U06", "PO#s", "multipleRecordLinks", {"linkedTableId": TB, "isReversed": False}),
        fld("U07", "PO Number (from PO#s)", "multipleLookupValues", {"recordLinkFieldId": "fldU06", "fieldIdInLinkedTable": "fldV01", "isValid": True}),
        fld("U08", "Done", "checkbox", {"icon": "check", "color": "greenBright"}), fld("U09", HOSTILE)]},
    {"id": TB, "name": "PO#s", "primaryFieldId": "fldV01", "views": [], "fields": [fld("V01", "PO Number"), fld("V02", "Total", "number")]},
    {"id": TC, "name": "1.2.3 Archive", "primaryFieldId": "fldW01", "views": [], "fields": [fld("W01", "Title")]}]}
post("/api/db/create", {"path": "names_ui.db"})
post("/api/db/connect", {"path": "names_ui.db"})
post("/api/schema/ingest", {"base_id": BASE, "source": "json", "schema_json": json.dumps(schema)})
cx0 = sqlite3.connect("names_ui.db"); cx0.executescript("DROP TABLE field_group_members; DROP TABLE field_groups;"); cx0.commit(); cx0.close()      # drop them to test the not-applied path
os.makedirs("data_extract/" + BASE, exist_ok=True)
json.dump({"format": "airtable-table-extract/1", "base_id": BASE, "table_id": TA, "table_name": "Orders", "captured_at": "2026-01-01T00:00:00Z",
           "fields": [{"field_id": "fldU02"}, {"field_id": "fldU03"}],
           "records": [{"id": "recUUU01", "fields": {"fldU02": "UISECRET-A", "fldU03": "UISECRET-A"}}, {"id": "recUUU02", "fields": {"fldU02": "UISECRET-B", "fldU03": "UISECRET-C"}},
                       {"id": "recUUU03", "fields": {"fldU02": "UISECRET-D"}}],
           "record_count": 3}, open("data_extract/%s/%s.json" % (BASE, TA), "w", encoding="utf-8"))

with sync_playwright() as p:
    br = p.chromium.launch()
    ctx = br.new_context(accept_downloads=True)
    pg = ctx.new_page()
    pg.set_default_timeout(10000)
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("dialog", lambda d: d.dismiss())

    def until(fn, what, secs=10):
        end = time.time() + secs
        last = None
        while time.time() < end:
            try:
                last = fn()
                if last:
                    return last
            except Exception as e:
                last = e
            time.sleep(0.15)
        raise AssertionError("timed out waiting for: %s (%s)" % (what, last))

    def text(sel):
        return pg.inner_text(sel)

    def msg(sel, part):
        until(lambda: part in text(sel), "%s to contain %r" % (sel, part))
        return True

    def trow(name):                       # a row of the table-names grid
        return pg.locator("#tablesGrid tbody tr:not(.reason)").filter(has_text=name).first

    def frow(name):                       # a row of the field grid
        return pg.locator("#fieldsGrid tbody tr:not(.reason)").filter(has_text=name).first

    pg.goto(B + "/names")
    pg.fill("#baseId", BASE)
    until(lambda: pg.locator("#tablesGrid tbody tr").count() == 3, "three tables to load")
    check("the page loads, Field names is the current nav entry, no script errors", "Field names" in text("a.nav-link.current") and not errs, errs)
    check("the gate is closed: field review is locked and nothing is approved", pg.is_visible("#fieldsLocked") and not pg.is_visible("#fieldsBody") and "3 of 3" in text("#gateNote"))
    check("every table shows as undecided", pg.locator("#tablesGrid tbody tr").filter(has_text="undecided").count() == 3)
    check("the settings grid has one column per name", pg.locator("#settingsGrid thead th").count() == 4 and pg.is_visible("#o_core_name_max_len") and not pg.is_visible("#o_core_label_leading_digits"))

    # ---- decisions need initials
    pg.click("#btnApproveUnchanged")
    msg("#tablesMessage", "initials")
    check("a decision without initials is refused with a message", "initials" in text("#tablesMessage").lower())
    pg.fill("#actor", "JV")
    pg.click("#btnApproveUnchanged")
    msg("#tablesMessage", "approved")
    until(lambda: "Orders" in text("#gateNote") or "2 of 3" in text("#gateNote"), "one table approved")
    check("bulk approve unchanged: one table approved, gate still closed", "2 of 3" in text("#gateNote") and pg.is_visible("#fieldsLocked"))
    pg.click("#btnApproveAutoTables")
    until(lambda: "1 of 3" in text("#gateNote"), "auto-fixable table approved")
    row = trow("1.2.3 Archive")
    check("a needs-a-person table offers its proposal in an editable box", row.locator("input").input_value() == "Archive 1-2-3")
    row.locator("input").fill("Archive (old)")
    row.get_by_role("button", name="Approve", exact=True).click()
    msg("#tablesMessage", "cannot be approved")
    check("an invalid typed table name is refused with the reason", "Special" in text("#tablesMessage"), text("#tablesMessage"))
    row.locator("input").fill("Archive 1-2-3")
    row.get_by_role("button", name="Approve", exact=True).click()
    until(lambda: pg.is_visible("#fieldsBody"), "the gate to open")
    check("approving the last table opens field review", "approved" in text("#gateNote").lower() and not pg.is_visible("#fieldsLocked") and pg.locator("#fieldsGrid tbody tr").count() >= 9)

    # ---- consolidation needs migration 009: without it the page says so; then apply the proposed DDL to this test database only
    check("without migration 009 the consolidation panel says how to enable it", pg.is_visible("#consNeeds") and pg.is_hidden("#consBody") and "migration 009" in text("#consNeeds"))
    cx = sqlite3.connect("names_ui.db"); cx.executescript(open("sql/migration_009_add_field_groups.sql", encoding="utf-8").read()); cx.commit(); cx.close()
    pg.reload()
    pg.fill("#baseId", BASE)
    until(lambda: pg.is_visible("#fieldsBody") and pg.is_visible("#consBody"), "the page to reload with consolidation available")
    check("with the tables the consolidation panel is available", pg.is_hidden("#consNeeds") and pg.is_disabled("#btnGCreate") and pg.is_disabled("#btnGCompare"))

    # ---- a hostile field name is shown as text and never runs
    check("a hostile field name is displayed as text and does not run", pg.evaluate("window.__xss") is None and frow("onerror").count() == 1 and pg.locator("#fieldsGrid img").count() == 0)

    # ---- preview versus saved settings
    pg.uncheck("#o_planning_name_char_cleanup")
    until(lambda: pg.is_visible("#previewNote"), "the preview note")
    check("changing a setting previews it: a note appears and approval buttons are off", pg.is_disabled("#btnApprovePass") and not pg.is_disabled("#btnSaveSettings") and "Preview" in text("#settingsState"))
    pg.check("#o_planning_name_char_cleanup")
    until(lambda: not pg.is_visible("#previewNote"), "the preview note to go away")
    check("putting the setting back clears the preview", not pg.is_disabled("#btnApprovePass") and "Defaults" in text("#settingsState"))
    pg.select_option("#o_planning_name_emoji_mode", "word")
    until(lambda: pg.is_visible("#previewNote"), "preview for the emoji option")
    until(lambda: "Hot Beverage" in text("#fieldsGrid"), "the emoji replaced with its word in the proposal")
    check("the emoji option previews: the proposal spells the emoji out", "Hot Beverage" in frow("Caf").inner_text(), frow("Caf").inner_text())
    pg.select_option("#o_planning_name_emoji_mode", "remove")
    until(lambda: not pg.is_visible("#previewNote"), "the preview to clear")
    pg.fill("#o_core_name_max_len", "40")
    until(lambda: pg.is_visible("#previewNote"), "the preview for max length")
    pg.click("#btnSaveSettings")
    until(lambda: "Saved." in text("#settingsState"), "settings to save")
    check("saved settings clear the preview and persist", not pg.is_visible("#previewNote") and pg.input_value("#o_core_name_max_len") == "40")
    pg.reload()
    pg.fill("#baseId", BASE)
    until(lambda: pg.is_visible("#fieldsBody"), "the page to reload")
    check("saved settings come back after a reload; the reviewer's initials are remembered", pg.input_value("#o_core_name_max_len") == "40" and pg.input_value("#actor") == "JV")
    pg.fill("#o_core_name_max_len", "255")
    pg.click("#btnSaveSettings")
    until(lambda: "Saved." in text("#settingsState"), "settings restored")

    # ---- the grid: tabs, filters, approvals
    check("three tabs, Planning name first", pg.locator("#targetTabs button").count() == 3 and "primary" in pg.locator("#targetTabs button").first.get_attribute("class"))
    pg.select_option("#fVerdict", "NEEDS_HUMAN")
    until(lambda: pg.locator("#fieldsGrid tbody tr:not(.reason)").count() > 0, "rows")
    rows = pg.locator("#fieldsGrid tbody tr:not(.reason)")
    check("the verdict filter shows only names that need a person", all("Needs a person" in rows.nth(i).inner_text() for i in range(rows.count())) and rows.count() >= 3)
    pg.select_option("#fVerdict", "")
    pg.fill("#fSearch", "Notes")
    until(lambda: pg.locator("#fieldsGrid tbody tr:not(.reason)").count() == 1, "the search to narrow the grid")
    check("search narrows the grid", "Notes!" in text("#fieldsGrid tbody"))
    frow("Notes!").get_by_role("button", name="Approve", exact=True).click()
    msg("#fieldsMessage", "approved")
    until(lambda: "Notes" in frow("Notes!").inner_text() and "Approved" in frow("Notes!").inner_text(), "the row to show Approved")
    check("approving a row stores the repaired name", frow("Notes!").locator("input").input_value() == "Notes")
    pg.fill("#fSearch", "")
    approved_rows = lambda: pg.locator("#fieldsGrid tbody tr:not(.reason)").filter(has_text="Approved").count()
    before = approved_rows()
    pg.click("#btnApprovePass")
    until(lambda: "name(s) approved" in text("#fieldsMessage"), "bulk approval")
    until(lambda: approved_rows() > before, "more rows to show Approved")
    check("approve-all-that-pass approves clean names and reports a count", approved_rows() > before and "name(s) approved" in text("#fieldsMessage"), (before, approved_rows()))
    pg.locator("#targetTabs button").nth(1).click()
    until(lambda: "primary" in pg.locator("#targetTabs button").nth(1).get_attribute("class"), "the Core name tab")
    check("the tabs switch to another name's grid", pg.locator("#fieldsGrid tbody tr").count() >= 9)
    pg.locator("#targetTabs button").first.click()

    # ---- duplicate groups and comparing their values
    check("a duplicate group is shown per name, with its members", pg.is_visible("#dupCard") and pg.locator("#dupBody > .note.warn").count() == 2 and "Amount" in text("#dupBody") and pg.locator("#dupBody tbody tr").count() >= 4)
    pg.get_by_role("button", name="Compare values").first.click()
    until(lambda: "Compared 3 saved records" in text("#dupBody"), "the value comparison")
    cmp_txt = text("#dupBody")
    check("compare values shows counts, a verdict and record IDs, never a value", "conflict" in cmp_txt.lower() and "recUUU02" in cmp_txt and "UISECRET" not in pg.inner_text("body") and "UISECRET" not in pg.content())
    members = pg.locator("#dupBody .note.warn").first.locator("table.grid").last.locator("tbody tr")      # the members table (the comparison table is above it)
    members.nth(1).locator("input").fill("Amount Archive")
    members.nth(1).get_by_role("button", name="Approve", exact=True).click()
    until(lambda: pg.locator("#dupBody > .note.warn").count() == 1, "the Planning name group to resolve")
    check("renaming one member resolves that name's group; the Core name group remains until it is handled too", "Core name in Orders" in text("#dupBody") and "Planning name in Orders" not in text("#dupBody"))

    # ---- consolidation: select fields, compare, consolidate, see the effects, dissolve
    core_block = pg.locator("#dupBody > .note.warn").first
    core_block.get_by_role("button", name="Consolidate these").click()
    until(lambda: pg.locator("#gFields input:checked").count() == 2, "the duplicate members to be preselected")
    check("'Consolidate these' preselects the group's fields in the panel", pg.input_value("#gTable") == TA and not pg.is_disabled("#btnGCompare") and pg.locator("#gSurvivor option").count() == 2)
    pg.click("#btnGCompare")
    until(lambda: "Compared 3 saved records" in text("#gCompare"), "the panel comparison")
    check("the panel compares the selected fields' values (counts only)", "UISECRET" not in pg.inner_text("body"))
    pg.click("#btnGCreate")
    msg("#gMessage", "reason")
    check("consolidating needs a reason", "reason" in text("#gMessage").lower() and pg.locator("#groupsList .note.ok").count() == 0)
    pg.select_option("#gRule", "survivor_wins")
    pg.fill("#gReason", "the same data under two names")
    pg.click("#btnGCreate")
    until(lambda: pg.locator("#groupsList .note.ok").count() == 1, "the group to be created")
    check("the group is listed with its conflict rule, who and why", "survives" in text("#groupsList") and "survivor's value" in text("#groupsList") and "the same data under two names" in text("#groupsList"))
    until(lambda: "No duplicate names remain." in text("#dupBody"), "the duplicate groups to clear")
    check("the merged field leaves the duplicate groups (both names)", "No duplicate names remain." in text("#dupBody"))
    pg.fill("#fSearch", "amount")
    until(lambda: pg.locator("#fieldsGrid tbody tr:not(.reason)").count() == 1, "the grid to hide the merged field")
    check("the merged field is hidden from the grid by default", pg.locator("#fieldsGrid tbody tr:not(.reason)").count() == 1)
    pg.check("#fAbandoned")
    until(lambda: pg.locator("#fieldsGrid tbody tr:not(.reason)").count() == 2, "the merged field to be listed")
    check("it is listed, marked consolidated, when abandoned and consolidated fields are shown", "Consolidated into" in text("#fieldsGrid tbody") and pg.get_by_role("button", name="Dissolve group").count() == 1)
    pg.uncheck("#fAbandoned")
    pg.fill("#fSearch", "")
    check("the survivor's description lists both origins", "Orders.amount" in pg.locator("#descGrid tbody tr").filter(has_text="Amount").first.locator("textarea").input_value())
    pg.locator("#groupsList").get_by_role("button", name="Dissolve", exact=True).click()
    until(lambda: pg.locator("#groupsList tr.reason").count() == 1, "the dissolve form")
    pg.locator("#groupsList tr.reason").get_by_role("button", name="Confirm").click()
    msg("#gMessage", "reason")
    check("dissolving needs a reason", "reason" in text("#gMessage").lower() and pg.locator("#groupsList .note.ok").count() == 1)
    pg.locator("#groupsList tr.reason input").fill("different data after all")
    pg.locator("#groupsList tr.reason").get_by_role("button", name="Confirm").click()
    until(lambda: pg.locator("#groupsList .note.ok").count() == 0, "the group to be dissolved")
    until(lambda: "Core name in Orders" in text("#dupBody"), "the duplicate group to return")
    check("dissolving returns the fields to the review and the duplicate group comes back", "Core name in Orders" in text("#dupBody") and "Orders.amount" not in pg.locator("#descGrid tbody tr").filter(has_text="Amount").first.locator("textarea").input_value())

    # ---- withdraw needs a reason
    pg.fill("#fSearch", "Notes")
    until(lambda: pg.locator("#fieldsGrid tbody tr:not(.reason)").count() == 1, "search")
    frow("Notes!").get_by_role("button", name="Withdraw").click()
    until(lambda: pg.locator("#fieldsGrid tr.reason").count() == 1, "the reason form")
    pg.locator("#fieldsGrid tr.reason").get_by_role("button", name="Confirm").click()
    msg("#fieldsMessage", "reason")
    check("withdrawing without a reason is refused", pg.locator("#fieldsGrid tr.reason").count() == 1 or "reason" in text("#fieldsMessage").lower())
    pg.locator("#fieldsGrid tr.reason input").fill("client wants to see it first")
    pg.locator("#fieldsGrid tr.reason").get_by_role("button", name="Confirm").click()
    until(lambda: "Unreviewed" in frow("Notes!").inner_text(), "the approval to be withdrawn")
    check("with a reason the approval is withdrawn", "Unreviewed" in frow("Notes!").inner_text())
    pg.fill("#fSearch", "")
    check("Abandon is not offered for a field that was never profiled", pg.get_by_role("button", name="Abandon").count() == 0)

    # ---- import
    csv_text = "field_id,old_name,new_name,new_label,old_dataType,new_dataType\r\nfldW01,Title,Title Text,Title Text,Single line text,Text\r\nfldNOPE,x,x,x,x,x\r\n"
    pg.click("#btnImport")
    check("importing with no file says so", "Choose a CSV" in text("#importResult"))
    pg.set_input_files("#importFile", files=[{"name": "renames.csv", "mimeType": "text/csv", "buffer": csv_text.encode()}])
    pg.click("#btnImport")
    until(lambda: "Imported" in text("#importResult"), "the import result")
    check("import reports what it did and lists the problems", "unmatched id" in text("#importResult").lower() and "missing from import" in text("#importResult").lower(), text("#importResult")[:200])
    pg.locator("#targetTabs button").nth(1).click()
    pg.fill("#fSearch", "Title")
    until(lambda: pg.locator("#fieldsGrid tbody tr:not(.reason)").count() == 1, "search")
    check("an imported name appears as the name being checked (an already approved Planning name is kept)", "Title Text" in text("#fieldsGrid tbody"))
    pg.fill("#fSearch", "")
    pg.locator("#targetTabs button").first.click()

    # ---- descriptions
    check("each field has a generated lineage description", pg.locator("#descGrid textarea").first.input_value().startswith("Source: Airtable. Origins:"))
    drow = pg.locator("#descGrid tbody tr").filter(has_text="Order Name").first
    check("Save is off until the text changes", drow.get_by_role("button", name="Save").is_disabled() and drow.get_by_role("button", name="Reset").is_disabled())
    drow.locator("textarea").fill("Source: Airtable. Origins: Orders.Order Name; Orders.Notes!. Merged by hand.")
    drow.get_by_role("button", name="Save").click()
    msg("#descMessage", "saved")
    until(lambda: "Edited by hand" in pg.locator("#descGrid tbody tr").filter(has_text="Order Name").first.inner_text(), "the edited tag")
    pg.locator("#descGrid tbody tr").filter(has_text="Order Name").first.get_by_role("button", name="Reset").click()
    until(lambda: "Generated" in pg.locator("#descGrid tbody tr").filter(has_text="Order Name").first.inner_text(), "the reset")
    check("a hand-edited description can be saved and reset to the generated one", "Generated" in pg.locator("#descGrid tbody tr").filter(has_text="Order Name").first.inner_text())
    pg.fill("#dSearch", "no such text anywhere")
    check("the description filter narrows the list", pg.locator("#descGrid tbody tr").count() == 0)
    pg.fill("#dSearch", "")

    # ---- scope: out, reason required, warning for links to an out table, clear
    po = trow("PO#s")
    po.get_by_role("button", name="Out").click()
    until(lambda: pg.locator("#tablesGrid tr.reason").count() == 1, "the scope reason form")
    pg.locator("#tablesGrid tr.reason").get_by_role("button", name="Confirm").click()
    msg("#tablesMessage", "reason")
    check("taking a table out of scope needs a reason", "reason" in text("#tablesMessage").lower())
    pg.locator("#tablesGrid tr.reason input").fill("purchase orders are not migrating")
    pg.locator("#tablesGrid tr.reason").get_by_role("button", name="Confirm").click()
    until(lambda: pg.locator("#scopeWarnings .note").count() == 1, "the out-of-scope warning")
    check("an out-of-scope table is skipped and its links are flagged for a decision", "out of scope" in text("#scopeWarnings") and "PO#s" in text("#scopeWarnings") and pg.locator("#tablesGrid tbody tr").filter(has_text="Out of scope").count() == 1)
    pg.locator("#tablesGrid tbody tr").filter(has_text="Out of scope").get_by_role("button", name="Clear").click()
    until(lambda: pg.locator("#scopeWarnings .note").count() == 0, "the warning to clear")
    check("clearing returns the table to undecided and removes the warning", pg.locator("#tablesGrid tbody tr").filter(has_text="undecided").count() == 3)
    orders = trow("Orders")
    orders.get_by_role("button", name="In", exact=True).click()
    until(lambda: trow("Orders").locator("select").count() == 1, "the target selector")
    trow("Orders").locator("select").select_option("planning_and_core")
    msg("#tablesMessage", "Target saved")
    check("an in-scope table can be given a target", trow("Orders").locator("select").input_value() == "planning_and_core")

    # ---- reports
    pg.click("text=Preview report")
    until(lambda: pg.is_visible("#reportFrame"), "the report frame")
    frame = pg.frame_locator("#reportFrame")
    until(lambda: "Field and table name review" in frame.locator("h1").inner_text(), "the report to load")
    check("the report previews in the page with its sections", "Review sign-off" in frame.locator("body").inner_text() and "Table names" in frame.locator("body").inner_text())
    check("the hostile name is escaped in the report too", frame.locator("img").count() == 0 and "onerror" in frame.locator("body").inner_text())
    with pg.expect_download() as dl:
        pg.click("text=Download report .html")
    check("the report downloads as an .html file", dl.value.suggested_filename.endswith(".html") and dl.value.suggested_filename.startswith("field-name-review_"), dl.value.suggested_filename)
    with pg.expect_download() as dl2:
        pg.click("text=Download all (zip)")
    check("all the CSVs download as a zip", dl2.value.suggested_filename.endswith(".zip"))

    check("no script errors anywhere in the whole session", not errs, errs)
    br.close()
shutil.rmtree("data_extract", ignore_errors=True)
raise SystemExit(0 if ok else 1)
