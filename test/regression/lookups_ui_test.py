"""Browser test of the Lookup lists page (/lookups): drives the real page in Chromium. Fabricated names only."""
import json, os, sqlite3, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src"))
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    raise SystemExit("SKIP playwright is not installed (pip install playwright; playwright install chromium)")
import typesfixture as tf
B = "http://127.0.0.1:8765"
BASE = "appLookupUi0001"
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


def choices(names):
    return {"choices": [{"id": "sel%03d" % i, "name": n} for i, n in enumerate(names)]}


HOSTILE = '<img src=x onerror="window.__xss=1">'
PLACES = ["Place %02d" % i for i in range(1, 13)] + ["place 01"]            # 13 options; "Place 01" and "place 01" repeat once case is ignored
schema = {"tables": [
    {"id": "tblLu0000000001", "name": "Orders", "primaryFieldId": "fldP01", "views": [], "fields": [
        fld("P01", "Order Name"), fld("P02", "Ship To", "multipleSelects", choices(PLACES)), fld("P03", "Status", "singleSelect", choices(["Open", "Closed"])),
        fld("P04", HOSTILE, "singleSelect", choices(["Place %02d" % i for i in range(1, 9)]))]}]}
post("/api/db/create", {"path": "lookups_ui.db"})
post("/api/db/connect", {"path": "lookups_ui.db"})
post("/api/schema/ingest", {"base_id": BASE, "source": "json", "schema_json": json.dumps(schema)})
cx = sqlite3.connect("lookups_ui.db"); tf.apply_lookup_migration(cx); cx.close()

with sync_playwright() as p:
    br = p.chromium.launch()
    pg = br.new_context().new_page()
    pg.set_default_timeout(10000)
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("dialog", lambda d: d.dismiss())

    def until(fn, what, secs=10):
        end, last = time.time() + secs, None
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

    def act(selector, msg_id, expect):
        """Click, then wait for the page's own answer; the old message is cleared first so a stale one cannot match."""
        pg.evaluate("(id) => { document.getElementById(id).textContent = ''; }", msg_id)
        pg.click(selector)
        until(lambda: expect in text("#" + msg_id), expect)

    def rows(grid):
        return pg.eval_on_selector_all("#%s tbody tr" % grid, "rs => rs.map(r => Array.from(r.cells).map(c => c.textContent))")

    pg.goto(B + "/lookups")
    pg.fill("#baseId", BASE)
    pg.fill("#threshold", "10"); pg.dispatch_event("#threshold", "change")          # the page starts at 50; the fabricated long list has 13 options
    until(lambda: len(rows("fieldsGrid")) >= 1 and "Ship To" in text("#fieldsGrid"), "the long field is listed")
    check("the page loads with Lookup lists current in the navigation and no script errors", "Lookup lists" in text("a.nav-link.current") and not errs, errs)
    nav = pg.eval_on_selector_all("#sidenav a", "as => as.map(a => a.querySelector('.nav-label').textContent)")
    check("the navigation lists it before Load a table", "Lookup lists" in nav and nav.index("Lookup lists") < nav.index("Load a table"), nav)
    check("only the long list is shown by default, with its option count, the repeat and the flag", len(rows("fieldsGrid")) == 1 and "13 option(s)" in text("#fieldsGrid") and "1 repeat" in text("#fieldsGrid") and "long list" in text("#fieldsGrid"), rows("fieldsGrid"))
    check("the summary counts the long lists and the repeat", "1 are long lists" in text("#summaryStrip") and "repeat" in text("#summaryStrip"), text("#summaryStrip"))
    pg.select_option("#fShow", "all")
    check("all select fields can be shown; the hostile field name is plain text and nothing ran", len(rows("fieldsGrid")) == 3 and HOSTILE in text("#fieldsGrid") and pg.evaluate("window.__xss === undefined"))
    pg.select_option("#fShow", "long")

    # ---- deciding needs initials and a reason
    pg.check("#fieldsGrid tbody tr:first-child input[type=checkbox]")
    check("ticking a field proposes a list name from the field name and opens the button", pg.input_value("#newName") == "Ship To" and not pg.is_disabled("#btnAssign"), pg.input_value("#newName"))
    pg.click("#btnAssign")
    check("without initials nothing is saved and the page says why", "initials" in text("#fieldsMessage") and rows("listsGrid")[0][0] == "No lookup list yet. Tick fields below and authorize them.", text("#fieldsMessage"))
    pg.fill("#actor", "JV")
    pg.click("#btnAssign")
    check("without a reason nothing is saved either", "reason" in text("#fieldsMessage"))
    pg.fill("#reason", "too long to be a pick list")
    pg.fill("#newName", "Orders")
    act("#btnAssign", "fieldsMessage", "table Orders")
    check("a list name equal to a table's name is refused in plain words", "table Orders" in text("#fieldsMessage"), text("#fieldsMessage"))
    pg.fill("#newName", "Places")
    act("#btnAssign", "fieldsMessage", "authorized as a lookup list")
    lr = rows("listsGrid")
    check("the list appears with its field, option count (case folded: 12) and who decided", len(lr) == 1 and "Orders.Ship To" in lr[0][1] and lr[0][2] == "12" and lr[0][3].startswith("JV"), lr)
    pg.select_option("#fShow", "authorized")
    fr = rows("fieldsGrid")
    check("the field shows its lookup list and the decision with the reason", len(fr) == 1 and "Places" in fr[0][5] and "JV" in fr[0][6] and "too long to be a pick list" in fr[0][6], fr)

    # ---- sharing the list, renaming, returning, deleting
    pg.select_option("#fShow", "all")
    pg.check("#fieldsGrid tbody tr:nth-child(2) input[type=checkbox]")
    pg.select_option("#targetList", label="Places")
    check("choosing an existing list hides the new-name box", not pg.is_visible("#newName"))
    act("#btnAssign", "fieldsMessage", "1 field(s) authorized")
    check("two fields now share the list", "; " in rows("listsGrid")[0][1], rows("listsGrid"))
    pg.fill("#listsGrid tbody tr:first-child input[type=text]", "Locations")
    act("#listsGrid tbody tr:first-child >> text=Rename", "listsMessage", "List renamed")
    check("a list that fields still use cannot be deleted", pg.is_disabled("#listsGrid tbody tr:first-child >> text=Delete"))
    pg.select_option("#fShow", "authorized")
    pg.check("#tickAll")
    act("#btnUnassign", "fieldsMessage", "returned to ordinary select fields")
    act("#listsGrid tbody tr:first-child >> text=Delete", "listsMessage", "List deleted")
    check("the list is gone and the history keeps every step", rows("listsGrid")[0][0].startswith("No lookup list") and
          [r[0] for r in sqlite3.connect("lookups_ui.db").execute("select event_type from lookup_list_events order by event_id")] == ["list_create", "assign", "assign", "list_rename", "unassign", "unassign", "list_delete"])

    # ---- keyboard and layout
    check("every scrolling window on the page can take keyboard focus, so the arrow keys pan it", pg.eval_on_selector_all(".tablewin", "els => els.length > 0 && els.every(e => e.getAttribute('tabindex') === '0' && e.getAttribute('role') === 'region' && !!e.getAttribute('aria-label'))"))
    for gid in ("listsGrid", "fieldsGrid"):
        got = pg.evaluate("(id) => { const t = document.getElementById(id), w = t.parentElement, th = t.tHead.rows[0].cells[0]; return [getComputedStyle(w).overflowY, getComputedStyle(th).position]; }", gid)
        check("%s heading is sticky inside a scrolling window" % gid, got == ["auto", "sticky"], got)
    check("no script errors anywhere in the whole session", not errs, errs)
    br.close()
raise SystemExit(0 if ok else 1)
