"""Tests for sharing a Planning record type and placing it in the sidebar (config validation, request bodies, verify-after-share, the whole-array section merge, failure paths,
reuse, and the share-record-types command) against a FAKE Workfront. Fabricated IDs and names; fake secrets; no network."""
import copy, io, json, os, sqlite3, sys, tempfile, urllib.error
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
import loadconfig as lc, loader, loadrun as lr, loadstate as ls, planningtransport as pt, tableload as tl, workfronthttp as wh
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else str(x)[:500])

TOKEN = "fakePLANtoken0123456789"
WS = "wsDev"
SHARE = [{"subjectId": "u-manager", "subjectType": "user", "role": "MANAGER_ROLE_X"}, {"subjectId": "g-team", "subjectType": "group", "role": "EDITOR_ROLE_Y"}]
SHARING = {"share_with": SHARE, "section_name": "Imported from Airtable"}
BLOCK = {"base_url": "https://acme.my.workfront.com", "bearer_token": TOKEN, "workspace_id": WS}

# ------------------------------------------------------------------ config validation
def cfg(**extra):
    return {"workfront": {"planning": {"dev": dict(BLOCK, **extra)}}}
def problem(**extra):
    try:
        lc.planning_sharing(cfg(**extra), "dev"); return None
    except lc.ConfigError as e:
        return str(e)
check("a stage with neither key is simply not configured (None)", lc.planning_sharing(cfg(), "dev") is None)
check("a good setting is returned trimmed, in order", lc.planning_sharing(cfg(share_with=[dict(s, subjectId=" " + s["subjectId"] + " ") for s in SHARE], section_name=" Imported from Airtable "), "dev") == SHARING)
check("share_with without section_name is refused, saying which is missing", "section_name is missing" in (problem(share_with=SHARE) or ""))
check("section_name without share_with is refused", "share_with is missing" in (problem(section_name="S") or ""))
check("an empty list is refused", "at least one" in (problem(share_with=[], section_name="S") or ""))
check("a list that is not a list is refused", "at least one" in (problem(share_with="u-manager", section_name="S") or ""))
check("an item that is not an object is refused", "item 1 must be an object" in (problem(share_with=["u-manager"], section_name="S") or ""))
for key in ("subjectId", "subjectType", "role"):
    check("an item without %s is refused and says so" % key, key in (problem(share_with=[{k: v for k, v in SHARE[0].items() if k != key}], section_name="S") or ""))
check("a placeholder role is refused", "role" in (problem(share_with=[dict(SHARE[0], role="PASTE_ROLE_HERE")], section_name="S") or ""))
check("a blank section name is refused", "section_name" in (problem(share_with=SHARE, section_name="  ") or ""))
check("a key Planning would ignore is refused (a wrong body looks like success)", "permissionSettings" in (problem(share_with=[dict(SHARE[0], permissionSettings={})], section_name="S") or ""))
check("the example config's placeholders are refused until edited", "subjectId" in (problem(share_with=[dict(SHARE[0], subjectId="PASTE_USER_ID_HERE")], section_name="S") or ""))
check("no error message carries the token", TOKEN not in (problem(share_with=[], section_name="S") or ""))
ex = json.load(open(os.path.join(ROOT, "config.example.json"), encoding="utf-8"))["workfront"]["planning"]["dev"]
check("config.example.json shows share_with and section_name", set(ex["share_with"][0]) == {"subjectId", "subjectType", "role"} and "section_name" in ex)

# ------------------------------------------------------------------ a stateful fake Workfront
class Resp(object):
    def __init__(self, status, body=None): self.status, self._b = status, (b"" if body is None else json.dumps(body).encode())
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False
def http_error(url, status, code):
    return urllib.error.HTTPError(url, status, "x", {}, io.BytesIO(json.dumps({"code": code}).encode()))
class Fake(object):
    """Record types, their members (the creating API account is always one), and a Workspace with recordTypeSections. Switches make each step misbehave."""
    def __init__(self, sections=None, honor_grant=True, honor_patch=True, deny_grant=False, deny_patch=False, conflicts=0, hide_sections=False, partial_grant=False):
        self.reqs, self.n, self.members, self.alive = [], 0, {}, set()
        self.sections = copy.deepcopy(sections if sections is not None else [])
        self.honor_grant, self.honor_patch, self.deny_grant, self.deny_patch, self.conflicts, self.hide_sections, self.partial_grant = honor_grant, honor_patch, deny_grant, deny_patch, conflicts, hide_sections, partial_grant
    def add_type(self, rid, shared=()):
        self.alive.add(rid); self.members[rid] = [{"subjectId": "api-account", "subjectType": "user", "role": "OWNER_ROLE"}] + [dict(m) for m in shared]
    def open(self, req, timeout=None):
        body = json.loads(req.data) if req.data else None
        m, url = req.get_method(), req.full_url
        path = url.split("/maestro/api")[1].split("?")[0]
        self.reqs.append((m, path, body))
        parts = path.strip("/").split("/")
        if m == "POST" and path == "/v2/workspaces/%s/record-types" % WS:
            self.n += 1; rid = "RT%d" % self.n; self.add_type(rid)
            return Resp(201, {"id": rid, "displayName": body["displayName"]})
        if parts[:3] == ["v2", "permissions", "record-types"]:
            rid = parts[3]
            if rid not in self.alive: raise http_error(url, 404, "NOT_FOUND")
            if m == "GET" and parts[4:] == ["members"]: return Resp(200, {"members": copy.deepcopy(self.members[rid]), "inheritViewOnly": False})
            if m == "PATCH" and parts[4:] == ["members", "bulk"]:
                if self.deny_grant: raise http_error(url, 403, "FORBIDDEN")
                if self.honor_grant:                 # like the real endpoint: only the documented keys count, anything else is silently ignored
                    for g in (body.get("grant") or [])[: (1 if self.partial_grant else None)]: self.members[rid].append(dict(g))
                return Resp(204)
        if parts[:2] == ["v2", "workspaces"] and parts[2:3] == [WS] and len(parts) == 3:
            if m == "GET":
                w = {"id": WS, "name": "Dev Workspace"}
                if not self.hide_sections: w["recordTypeSections"] = copy.deepcopy(self.sections)
                return Resp(200, w)
            if m == "PATCH":
                if self.deny_patch: raise http_error(url, 400, "BAD_REQUEST")
                if self.conflicts: self.conflicts -= 1; raise http_error(url, 409, "WRITE_CONFLICT")
                if self.honor_patch and "recordTypeSections" in body: self.sections = copy.deepcopy(body["recordTypeSections"])
                return Resp(200, {"id": WS})
        raise http_error(url, 404, "NOT_FOUND")
    def of(self, method, tail): return [r for r in self.reqs if r[0] == method and r[1].endswith(tail)]

def transport(fk, sharing=SHARING):
    h = wh.Http(lc.credentials(cfg(), "planning", "dev"), {TOKEN}, opener=fk, sleep=lambda s: None)
    return pt.PlanningTransport(h, sharing)
RT_ITEM = {"temp_id": "P-T-tA", "system": "planning", "kind": "record_type", "name": "Orders", "label": "Orders", "data_type": None, "position": None, "parent": None, "description": "d"}
ORIG = [{"displayName": "Sales", "recordTypeIds": ["OLD1", "OLD2"], "id": "sec-1"}, {"displayName": "Imported from Airtable", "recordTypeIds": ["OLD3"], "id": "sec-2"}, {"displayName": "Ops", "recordTypeIds": []}]

# ------------------------------------------------------------------ create: share, verify, place
fk = Fake(sections=ORIG)
tr = transport(fk)
rid = tr.create(RT_ITEM, None)
check("create still returns the new ID", rid == "RT1")
grants = fk.of("PATCH", "/members/bulk")
check("one grant call, on the record type's members/bulk route", len(grants) == 1 and grants[0][1] == "/v2/permissions/record-types/RT1/members/bulk", fk.reqs)
check("the grant body is exactly {grant: [subjectId, subjectType, role], inheritViewOnly: false}", grants[0][2] == {"grant": SHARE, "inheritViewOnly": False}, grants[0][2])
check("no permissionSettings, id or type keys anywhere in the grant", set(grants[0][2]) == {"grant", "inheritViewOnly"} and all(set(g) == {"subjectId", "subjectType", "role"} for g in grants[0][2]["grant"]))
check("the grant is verified with a GET of the members, after the PATCH", [r[0] for r in fk.reqs if "/permissions/" in r[1]] == ["PATCH", "GET"], fk.reqs)
check("the Workspace is read, then patched with the WHOLE array, then read back", [r[0] for r in fk.reqs if r[1] == "/v2/workspaces/%s" % WS] == ["GET", "PATCH", "GET"], fk.reqs)
patch = fk.of("PATCH", "/v2/workspaces/wsDev")[0][2]
want = copy.deepcopy(ORIG); want[1]["recordTypeIds"].append("RT1")
check("the PATCH body holds only recordTypeSections, and it is the complete array with only the new ID added to the named section (nothing removed or reordered)", patch == {"recordTypeSections": want}, patch)
check("the section the Workspace now has matches", fk.sections == want)
r = tr.share_results[0]
check("the result says both steps are confirmed, with no problem", r["members"] == "ok" and r["section"] == "ok" and r["problems"] == [], r)
check("the token is not in any recorded result", TOKEN not in json.dumps(tr.share_results))

# the section is created when it does not exist; other sections stay as they were
fk = Fake(sections=[ORIG[0], ORIG[2]])
tr = transport(fk); tr.create(RT_ITEM, None)
check("a missing section is created at the end, nothing else changes", fk.sections == [ORIG[0], ORIG[2], {"displayName": "Imported from Airtable", "recordTypeIds": ["RT1"]}] and tr.share_results[0]["section"] == "ok" and any("created the sidebar section" in n for n in tr.share_results[0]["notes"]), fk.sections)
fk = Fake(sections=[])
tr = transport(fk); tr.create(RT_ITEM, None)
check("an empty array becomes the one new section", fk.sections == [{"displayName": "Imported from Airtable", "recordTypeIds": ["RT1"]}])
fk = Fake(sections=[dict(ORIG[1], displayName="  imported   from airtable ")])
tr = transport(fk); tr.create(RT_ITEM, None)
check("the section is found ignoring case and spacing, so no duplicate section is made", len(fk.sections) == 1 and fk.sections[0]["recordTypeIds"] == ["OLD3", "RT1"] and fk.sections[0]["id"] == "sec-2", fk.sections)
fk = Fake(sections=[{"displayName": "Imported from Airtable"}])
tr = transport(fk); tr.create(RT_ITEM, None)
check("a section with no recordTypeIds list gets one", fk.sections == [{"displayName": "Imported from Airtable", "recordTypeIds": ["RT1"]}], fk.sections)
# two record types in a row: the second read sees the first, so both stay
fk = Fake(sections=ORIG)
tr = transport(fk); a, b = tr.create(RT_ITEM, None), tr.create(dict(RT_ITEM, temp_id="P-T-tB", name="Customers"), None)
check("two record types in a row both end up in the section, in order", fk.sections[1]["recordTypeIds"] == ["OLD3", a, b] and fk.sections[0] == ORIG[0], fk.sections)

# ------------------------------------------------------------------ failure paths: never stop the run, always say so
fk = Fake(sections=ORIG, honor_grant=False)
tr = transport(fk); rid = tr.create(RT_ITEM, None)
r = tr.share_results[0]
check("a 204 that changed nothing is NOT taken as success: verify finds the subjects missing", rid == "RT1" and r["members"] == "failed" and any("NOT confirmed" in p and "u-manager" in p and "g-team" in p for p in r["problems"]), r)
check("...and the sidebar step still ran", r["section"] == "ok" and fk.sections[1]["recordTypeIds"] == ["OLD3", "RT1"])
fk = Fake(sections=ORIG, partial_grant=True)
tr = transport(fk); tr.create(RT_ITEM, None); r = tr.share_results[0]
check("only some subjects granted is a failure naming the one that is missing", r["members"] == "failed" and "g-team" in r["problems"][0] and "u-manager" not in r["problems"][0], r)
fk = Fake(sections=ORIG, deny_grant=True)
tr = transport(fk); rid = tr.create(RT_ITEM, None); r = tr.share_results[0]
check("a refused grant is reported plainly, the create still returns its ID, and the sidebar step still runs", rid == "RT1" and r["members"] == "failed" and "Sharing failed" in r["problems"][0] and "403" in r["problems"][0] and r["section"] == "ok", r)
fk = Fake(sections=ORIG, honor_patch=False)
tr = transport(fk); tr.create(RT_ITEM, None); r = tr.share_results[0]
check("a Workspace update that is accepted but did nothing is caught by reading it back", r["section"] == "failed" and any("NOT confirmed" in p for p in r["problems"]) and r["members"] == "ok", r)
fk = Fake(sections=ORIG, deny_patch=True)
tr = transport(fk); rid = tr.create(RT_ITEM, None); r = tr.share_results[0]
check("a refused Workspace update is reported, and the sharing step is unaffected", rid == "RT1" and r["section"] == "failed" and "Sidebar placement failed" in r["problems"][0] and r["members"] == "ok" and fk.sections == ORIG, r)
fk = Fake(sections=ORIG, hide_sections=True)
tr = transport(fk); tr.create(RT_ITEM, None); r = tr.share_results[0]
check("a Workspace reply with no recordTypeSections list is never patched (a partial array could hide other record types)", r["section"] == "failed" and fk.of("PATCH", "/v2/workspaces/wsDev") == [] and "left alone" in r["problems"][0], r)
fk = Fake(sections=ORIG, conflicts=1)
tr = transport(fk); tr.create(RT_ITEM, None); r = tr.share_results[0]
check("a write conflict (409) re-reads the array and tries again, with the whole array each time", r["section"] == "ok" and len(fk.of("PATCH", "/v2/workspaces/wsDev")) == 2 and fk.sections == want, fk.reqs)
fk = Fake(sections=ORIG, conflicts=3)
tr = transport(fk); tr.create(RT_ITEM, None); r = tr.share_results[0]
check("a conflict that never clears is reported after three tries", r["section"] == "failed" and len(fk.of("PATCH", "/v2/workspaces/wsDev")) == 3 and fk.sections == ORIG, r)
fk = Fake(sections=ORIG)
tr = transport(fk, None); rid = tr.create(RT_ITEM, None); r = tr.share_results[0]
check("with no sharing configured the record type is made, nothing is granted, and the result says NOT SHARED", rid == "RT1" and [x for x in fk.reqs if "/permissions/" in x[1] or x[0] == "PATCH"] == [] and r["problems"] and "NOT SHARED" in r["problems"][0], fk.reqs)

# ------------------------------------------------------------------ reuse: only what is missing, and a deleted record type is explained
fk = Fake(sections=ORIG); fk.add_type("RTX", shared=SHARE)
tr = transport(fk); r = tr.ensure_shared("RTX")
check("a reused record type that is already shared gets no grant call, but is still placed in the sidebar", fk.of("PATCH", "/members/bulk") == [] and r["members"] == "ok" and r["section"] == "ok" and fk.sections[1]["recordTypeIds"][-1] == "RTX", r)
fk = Fake(sections=ORIG); fk.add_type("RTX", shared=[SHARE[0]])
tr = transport(fk); r = tr.ensure_shared("RTX")
g = fk.of("PATCH", "/members/bulk")
check("a reused record type missing one subject is granted only that one", len(g) == 1 and g[0][2]["grant"] == [SHARE[1]] and r["members"] == "ok", g)
fk = Fake(sections=ORIG); fk.add_type("RTX", shared=SHARE)
tr = transport(fk); tr.ensure_shared("RTX"); n1 = len(fk.reqs); fk2 = fk.sections
tr.ensure_shared("RTX")
check("a second check changes nothing (already shared, already in its section)", fk.sections == fk2 and len(fk.of("PATCH", "/v2/workspaces/wsDev")) == 1 and fk.of("PATCH", "/members/bulk") == [])
fk = Fake(sections=ORIG)
tr = transport(fk); r = tr.ensure_shared("RT-DELETED")
check("a record type deleted in Workfront is explained, with the way out", r["members"] == "failed" and "planning-reset" in r["problems"][0] and "no such record type" in r["problems"][0], r)

# ------------------------------------------------------------------ the runner: created and reused record types
d = tempfile.mkdtemp(prefix="plshare_")
dbp = os.path.join(d, "t.db")
conn = sqlite3.connect(dbp); conn.row_factory = sqlite3.Row
conn.executescript(open(os.path.join(ROOT, "sql", "DatabaseSchemaLibrary.sql"), encoding="utf-8").read())
items = [dict(RT_ITEM, status="ready", reasons=[], table_id="tA", field_id=None, spec={}, display_type=None)]
env = lr.resolve_environment("dev", None, dry_run=False, workspace_id=WS)
fk = Fake(sections=ORIG)
tr = transport(fk)
res, _ = tl.run_structure(conn, "appL", items, "planning", tr, env, WS, dry_run=False)
check("run_structure creates, shares and places a new record type and returns the results", res["counts"] == {"created": 1} and len(res["sharing"]) == 1 and res["sharing"][0]["members"] == "ok" and res["sharing"][0]["section"] == "ok", res)
fk2 = Fake(sections=ORIG)
fk2.add_type("RT1", shared=[])                       # still there, but nobody was granted yet
tr2 = transport(fk2)
res, _ = tl.run_structure(conn, "appL", items, "planning", tr2, env, WS, dry_run=False)
check("a second run reuses the record type (nothing created) and now shares and places it", res["counts"] == {"already_created": 1} and fk2.of("POST", "/record-types") == [] and res["sharing"][0]["members"] == "ok" and fk2.sections[1]["recordTypeIds"][-1] == "RT1", res)
fkd = Fake(sections=ORIG)
res, _ = tl.run_structure(conn, "appL", [dict(items[0], temp_id="P-T-tNew", name="New table")], "planning", transport(fkd), env, WS, dry_run=True)
check("a dry run sends nothing and shares nothing", res["counts"] == {"would_create": 1} and res["sharing"] == [] and fkd.reqs == [], (res["counts"], fkd.reqs))

# ------------------------------------------------------------------ share-record-types (one-off, for record types already in the id map)
conn.execute("delete from load_id_map")
for t, rid2, name, ws in (("P-T-tA", "RTA", "Orders", WS), ("P-T-tB", "RTB", "Customers", WS), ("P-T-tC", "RTC", "Other workspace", "wsOther")):
    conn.execute("insert into load_id_map(temp_id, environment, workspace_id, workspace_key, system, kind, base_id, table_id, name, workfront_id, status) values (?,?,?,?,?,?,?,?,?,?,?)", (t, "dev", ws, ws, "planning", "record_type", "appL", t[-2:], name, rid2, "created"))
conn.commit(); conn.close()
cfgp = os.path.join(d, "config.json")
json.dump(cfg(share_with=SHARE, section_name="Imported from Airtable"), open(cfgp, "w"))
def refused(argv, **k):
    try:
        loader.main(argv, **k); return None
    except SystemExit as e:
        return str(e.code)
fk = Fake(sections=ORIG); fk.add_type("RTA"); fk.add_type("RTB", shared=SHARE)
out = loader.main(["share-record-types", "--db", dbp, "--out", os.path.join(d, "o1"), "--config", cfgp], opener=fk)
check("without --send it lists this Workspace's record types only and sends nothing", fk.reqs == [] and sorted(out["ids"]) == ["RTA", "RTB"] and out["results"] == [])
out = loader.main(["share-record-types", "--db", dbp, "--out", os.path.join(d, "o2"), "--config", cfgp, "--send"], opener=fk)
txt = open(os.path.join(d, "o2", "share_record_types_summary.txt"), encoding="utf-8-sig").read()
check("with --send each record type is checked, the missing grant made, and both are placed", [r["members"] for r in out["results"]] == ["ok", "ok"] and [r["section"] for r in out["results"]] == ["ok", "ok"] and len(fk.of("PATCH", "/members/bulk")) == 1
      and fk.sections[1]["recordTypeIds"] == ["OLD3", "RTA", "RTB"] and "2 shared and placed" in txt and "0 with a problem" in txt, txt)
check("the summary holds no token", TOKEN not in txt)
fk = Fake(sections=ORIG, honor_grant=False); fk.add_type("RTA"); fk.add_type("RTB")
loader.main(["share-record-types", "--db", dbp, "--out", os.path.join(d, "o3"), "--config", cfgp, "--send"], opener=fk)
txt = open(os.path.join(d, "o3", "share_record_types_summary.txt"), encoding="utf-8-sig").read()
check("a failure is shown plainly in the summary with the record type's ID", "PROBLEM RTA" in txt and "NOT confirmed" in txt and "2 with a problem" in txt, txt)
nocfg = os.path.join(d, "none.json"); json.dump(cfg(), open(nocfg, "w"))
check("with no share_with in config.json it refuses, saying what to add", "share_with and section_name" in (refused(["share-record-types", "--db", dbp, "--config", nocfg]) or ""))
half = os.path.join(d, "half.json"); json.dump(cfg(share_with=SHARE), open(half, "w"))
check("a half-set config is refused with a plain message", "section_name is missing" in (refused(["share-record-types", "--db", dbp, "--config", half]) or ""))
print("ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
