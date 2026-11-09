import json, os, re
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    raise SystemExit("SKIP playwright is not installed (pip install playwright; playwright install chromium)")
B="http://127.0.0.1:8765"; ok=True
def check(n,c,x=""):
    global ok; ok&=bool(c); print(("PASS " if c else "FAIL ")+n, "" if c else str(x)[:300])
with sync_playwright() as p:
    br=p.chromium.launch(); ctx=br.new_context(accept_downloads=True); pg=ctx.new_page()
    errs=[]; pg.on("console",lambda m: errs.append(m.text) if m.type=="error" else None); pg.on("pageerror",lambda e: errs.append(str(e)))
    import urllib.request
    rq=urllib.request.Request(B+"/api/db/create",method="POST",data=json.dumps({"path":"db/migration.db"}).encode(),headers={"Content-Type":"application/json"}); urllib.request.urlopen(rq).read()
    # ---- Setup is the home page: steps 1 and 2, left nav, shared DB badge ----
    pg.goto(B); pg.wait_for_timeout(1200)
    navtxt=lambda p: [a.inner_text().split("\n")[0] for a in p.query_selector_all("#sidenav .nav-label")]
    check("home page is Setup with steps 1 and 2 only", pg.query_selector("#step1") and pg.query_selector("#step2") and not pg.query_selector("#step3") and pg.inner_text("#sidenav a.current .nav-label")=="Setup")
    check("nav order, Stage 4 is a planned slot, log is last", navtxt(pg)==["Setup","Stage 1: Data","Stage 2: Forms","Stage 3: Automations","Stage 4: Map and load","API call log"], navtxt(pg))
    check("Stage 4 is not a link", pg.query_selector("#sidenav .planned") is not None and pg.query_selector("#sidenav .planned").get_attribute("href") is None)
    box=lambda sel: pg.eval_on_selector(sel,"e=>{const r=e.getBoundingClientRect();return [r.top,r.bottom]}")
    nb=pg.eval_on_selector("#sidenav","e=>{const r=e.getBoundingClientRect();return r.bottom}")
    check("API call log link sits at the bottom of the side panel, below Stage 4", box("#sidenav a[href='/log']")[0]>box("#sidenav .planned")[1] and nb-box("#sidenav a[href='/log']")[1]<40, (box("#sidenav a[href='/log']"),nb))
    check("database badge shows connected on Setup", "connected" in pg.inner_text("#dbBadge") and "not" not in pg.inner_text("#dbBadge"), pg.inner_text("#dbBadge"))
    check("Setup lists the connected database's tables", pg.query_selector_all("#countsTable tbody tr") and len(pg.query_selector_all("#countsTable tbody tr"))==15)
    check("token shown only as yes/no", pg.inner_text("#tokenYesNo")=="yes" and "patMOCK" not in pg.content())
    check("hidden rows are really hidden (no stray empty button on Setup)", not pg.is_visible("#dbActions"))
    pg.fill("#baseId","appSampleBase001"); pg.wait_for_timeout(200)
    pg.click("#sidenav a[href='/stage1']"); pg.wait_for_timeout(1500)
    check("nav click opens Stage 1 with it marked current", pg.url.endswith("/stage1") and pg.inner_text("#sidenav a.current .nav-label")=="Stage 1: Data", pg.url)
    check("database connection carried to Stage 1 (badge, no notice, buttons enabled)", "not" not in pg.inner_text("#dbBadge") and pg.is_hidden("#dbNotice") and not pg.is_disabled("#btnIngest") and not pg.is_disabled("#btnProfile"))
    check("attachment-download row stays hidden until Also save records is ticked", not pg.is_visible("#dlRow"))
    pg.check("#saveRecords"); check("...and appears when it is ticked", pg.is_visible("#dlRow")); pg.uncheck("#saveRecords")
    check("base chosen on Setup is preselected on Stage 1", pg.input_value("#baseId")=="appSampleBase001", pg.input_value("#baseId"))
    check("Stage 1 holds steps 3 to 6 and no step 1, 2 or log card", all(pg.query_selector("#step%d"%i) for i in (3,4,5,6)) and not pg.query_selector("#step1") and not pg.query_selector("#logCard"))
    pg.fill("#baseId","appSampleBase001"); pg.click("#btnIngest"); pg.wait_for_timeout(1500)
    labels=lambda sel:[o.inner_text() for o in pg.query_selector_all(sel+" option")]
    for sel in ["#scopeBase","#resBase","#csvBase","#exBase"]:
        l=labels(sel); check("%s uses Name - ID"%sel, "Sample Shop - appSampleBase001" in l, l)
    check("table dropdown carries the base label", any(o.startswith("Sample Shop - appSampleBase001 / ") for o in labels("#resTable")), labels("#resTable")[:3])
    # a base in db but not config: ingest base 2
    pg.fill("#baseId","appSampleBase002"); pg.click("#btnIngest"); pg.wait_for_timeout(1500)
    check("db-only base listed by ID", "appSampleBase002" in labels("#resBase"), labels("#resBase"))
    pg.fill("#baseId",""); pg.click("#baseToggle"); pg.wait_for_timeout(300)
    menu=pg.inner_text("#baseMenu"); check("combo offers config base and db-only base", "Sample Shop" in menu and "appSampleBase002" in menu, menu)
    # mode help
    pg.select_option("#depMode","literal"); t1=pg.inner_text("#depModeHelp")
    pg.select_option("#depMode","collapse"); t2=pg.inner_text("#depModeHelp")
    check("mode help differs and is italic note", t1.startswith("Literal") and t2.startswith("Resolve") and pg.eval_on_selector("#depModeHelp","e=>getComputedStyle(e).fontStyle")=="italic")
    order=pg.evaluate("()=>{const a=document.getElementById('depModeHelp'),b=document.getElementById('depMessage');return !!(a.compareDocumentPosition(b)&4)}")
    check("help sits above the Load order written message", order)
    pg.click("#btnRecompute"); pg.wait_for_timeout(1200)
    with pg.expect_download() as d: pg.click("#btnGraphSvg")
    path=d.value.path(); svg=open(path,encoding="utf-8").read()
    check("svg file name", re.match(r"^DependencyGraph_Resolve_\d{8}-\d{4}\.svg$", d.value.suggested_filename), d.value.suggested_filename)
    check("svg standalone: xmlns, inline style, white bg, nodes", 'xmlns="http://www.w3.org/2000/svg"' in svg and "<style>" in svg and 'fill="#ffffff"' in svg and svg.count('class="node')>=10, svg[:200])
    with pg.expect_download() as d2: pg.click("#btnGraphPng")
    png=open(d2.value.path(),"rb").read()
    check("png is a real PNG with size", png[:8]==b"\x89PNG\r\n\x1a\n" and len(png)>3000 and re.match(r"^DependencyGraph_Resolve_\d{8}-\d{4}\.png$", d2.value.suggested_filename), (len(png),d2.value.suggested_filename))
    open("graph_test.png","wb").write(png)
    # profile csv names
    pg.select_option("#scope","all"); pg.click("#btnProfile"); pg.wait_for_timeout(5000)
    def dl(sel):
        with pg.expect_download() as x: pg.click(sel)
        return x.value.suggested_filename
    pg.select_option("#resBase",""); pg.select_option("#resTable","")
    check("all/all name", re.match(r"^Bases_Tables_Profile_\d{8}-\d{4}\.csv$", dl("#lnkProfileCsv")))
    pg.select_option("#resBase","appSampleBase001")
    check("base only", re.match(r"^appSampleBase001_Tables_Profile_\d{8}-\d{4}\.csv$", dl("#lnkProfileCsv")))
    pg.select_option("#resTable",index=1)
    tid=pg.eval_on_selector("#resTable","e=>e.value")
    n=dl("#lnkProfileCsv"); check("table selected uses its ID", re.match(r"^[A-Za-z0-9]+_%s_Profile_\d{8}-\d{4}\.csv$"%tid, n), n)
    check("csv helper text mentions row, null, cardinality", all(w in pg.inner_text("#step5") for w in ["row count","null count","cardinality"]))
    # ---- API call log is its own page ----
    pg.click("#sidenav a[href='/log']"); pg.wait_for_timeout(1500)
    check("log page opens, marked current", pg.url.endswith("/log") and pg.inner_text("#sidenav a.current .nav-label")=="API call log")
    check("log page lists the calls profiling made", len(pg.query_selector_all("#logTableEl tbody tr"))>0 and "Matching calls" in pg.inner_text("#logSummary"), pg.inner_text("#logSummary"))
    pg.check("#logFail"); pg.wait_for_timeout(600)
    check("failures filter works on the log page", "Matching calls" in pg.inner_text("#logSummary"))
    # ---- placeholders ----
    for path,label,needs in [("/stage3","Stage 3: Automations","automation_catalog")]:
        pg.click("#sidenav a[href='%s']"%path); pg.wait_for_timeout(700)
        check("%s placeholder"%label, pg.inner_text("#sidenav a.current .nav-label")==label and "Not built yet" in pg.inner_text("main") and needs in pg.inner_text("main"))
    pg.click("#sidenav a[href='/stage2']"); pg.wait_for_timeout(900)
    check("Stage 2 is the form recorder, not a placeholder", pg.inner_text("#sidenav a.current .nav-label")=="Stage 2: Forms" and "Not built yet" not in pg.inner_text("main") and pg.query_selector("#pickCard") is not None)
    # ---- old address and a fresh tab: base choice survives within the tab, and the old URL lands on Setup ----
    pg.goto(B+"/index.html"); pg.wait_for_timeout(800)
    check("/index.html lands on Setup", pg.url.rstrip("/")==B and pg.inner_text("#sidenav a.current .nav-label")=="Setup", pg.url)
    check("base choice is still remembered after other pages", pg.input_value("#baseId")=="appSampleBase001", pg.input_value("#baseId"))
    # ---- no database connected: a stage page switches itself off and points to Setup ----
    p2=ctx.new_page(); errs2=[]; p2.on("pageerror",lambda e: errs2.append(str(e)))
    def nodb(route):
        d=route.fetch().json(); d["db_path"]=None
        route.fulfill(status=200,content_type="application/json",body=json.dumps(d))
    p2.route("**/api/status", nodb); p2.goto(B+"/stage1"); p2.wait_for_timeout(1200)
    check("Stage 1 without a database: notice with link to Setup, buttons disabled", p2.is_visible("#dbNotice") and p2.query_selector("#dbNotice a[href='/setup']") and p2.is_disabled("#btnIngest") and p2.is_disabled("#btnProfile") and p2.is_disabled("#btnRecompute"))
    p2.goto(B+"/log"); p2.wait_for_timeout(800)
    check("log page without a database: notice shown", p2.is_visible("#dbNotice") and p2.is_disabled("#btnLog"))
    check("no script errors without a database", not errs2, errs2)
    p2.close()
    check("no console errors", not errs, errs)
    pg.screenshot(path="ui_test.png",full_page=False)
    br.close()
print("ALL PASS" if ok else "SOME FAILED")
