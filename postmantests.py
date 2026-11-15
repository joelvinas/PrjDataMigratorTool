#!/usr/bin/env python3
"""The Postman test guide: five tests to run by hand in a DEV Workspace, and a Postman collection that holds the same requests. Standard library only.

The app never calls Workfront Planning for these and never sees a token: the person runs them in Postman, adds their own authorization there (how Planning
authenticates is not documented on the saved v2 reference page), and reads the result against the rules below. Paths, property names and limits are taken from the
saved Planning API v2 reference (base https://{customer-domain}/maestro/api); anything the reference does not say is marked as something the test is meant to find out.

Every record type, field, record and Workspace the tests create has a name that starts with ZZ and is throwaway. Run them in a DEV Workspace only, and finish with
the cleanup list (deleting a record type also deletes its records). Nothing here is random: the same call gives the same guide and the same collection.
"""
import json

BASE_URL = "https://{customer-domain}/maestro/api"
PREFIX = "ZZ Migration Test"
LONG_512 = "x" * 512
LONG_513 = "x" * 513
LONG_1024 = "x" * 1024
LONG_1025 = "x" * 1025
MARKER_EXAMPLE = 'Fusion-driven: written by scenario "Order sync". Source: Airtable. Origins: Orders.Total.'

# Postman collection variables, in the order a person sets them. The ones with a value are fixed test text; the rest the person fills in (or a step saves).
VARIABLES = [
    ("customer_domain", "", "Your Workfront host name, for example mycompany.my.workfront.com (no https://)."),
    ("workspace_id", "", "The ID of the DEV Workspace you run everything in."),
    ("workspace2_id", "", "A second, throwaway Workspace (test 1 can create one for you; the ID is saved by the step)."),
    ("rt_a_id", "", "Saved by the step that creates the first throwaway record type."),
    ("rt_a_dup_id", "", "Saved by test 1 if a second record type with the same name is allowed."),
    ("rt_a_lower_id", "", "Saved by test 1 if a record type differing only by letter case is allowed."),
    ("rt_b_id", "", "Saved by the step that creates the second throwaway record type."),
    ("rt_b2_id", "", "Saved by test 1 if the same name is allowed in the second Workspace."),
    ("field_id", "", "Saved by the step that creates a throwaway field."),
    ("primary_field_id", "", "Saved by test 5: the record type's primary field, which the test records are written into."),
    ("long_description_512", LONG_512, "512 characters, the documented maximum (test 3)."),
    ("long_description_513", LONG_513, "513 characters, one over the v2 reference maximum for a field description (test 3)."),
    ("long_description_1024", LONG_1024, "1,024 characters, the maximum quoted from the v3 PATCH documentation (test 3)."),
    ("long_description_1025", LONG_1025, "1,025 characters, one over that (test 3)."),
]


def _step(label, method, path, body=None, save=None, note=None, expect=None, optional=False):
    return {"label": label, "method": method, "path": path, "body": body, "save": save, "note": note, "expect": expect, "optional": optional}


TESTS = [
    {
        "id": 1, "key": "record-type-name-uniqueness", "title": "Record type name uniqueness",
        "purpose": "Settles whether the schema builder must check record type names per Workspace or across the organization. The v2 reference documents no name-conflict error for creating a record type, so this test finds out what actually happens.",
        "before": ["You are in a DEV Workspace (workspace_id is set).",
                   "You need a second Workspace. If you do not have a throwaway one, step 1 creates one; set workspace2_id by hand and skip step 1 if you do."],
        "steps": [
            _step("Create a throwaway second Workspace (skip if you already have one)", "POST", "/v2/workspaces", {"name": PREFIX + " Second Workspace"}, "workspace2_id", optional=True,
                  expect="201. The response id is saved as workspace2_id."),
            _step("Create the first record type", "POST", "/v2/workspaces/{{workspace_id}}/record-types", {"displayName": PREFIX + " Name"}, "rt_a_id",
                  expect="201. The response id is saved as rt_a_id."),
            _step("Create a second record type with the SAME name in the SAME Workspace", "POST", "/v2/workspaces/{{workspace_id}}/record-types", {"displayName": PREFIX + " Name"}, "rt_a_dup_id",
                  expect="Either an error (note its status and errorCode) or 201 (a duplicate was allowed)."),
            _step("Create a record type whose name differs only by letter case, in the same Workspace", "POST", "/v2/workspaces/{{workspace_id}}/record-types", {"displayName": PREFIX.lower() + " name"}, "rt_a_lower_id",
                  expect="An error means names are compared without regard to case; 201 means case counts.", optional=True),
            _step("Create the same name in the SECOND Workspace", "POST", "/v2/workspaces/{{workspace2_id}}/record-types", {"displayName": PREFIX + " Name"}, "rt_b2_id",
                  expect="Either an error (note its status and errorCode) or 201."),
        ],
        "results": [
            ("Step 3 is refused and step 5 succeeds", "Names are unique per Workspace. The schema builder checks each Workspace separately."),
            ("Step 3 is refused and step 5 is refused too", "Names are unique across the organization. The schema builder must check every record type name in the whole organization, not only the Workspace."),
            ("Steps 3 and 5 both succeed", "Nothing enforces uniqueness. Never use a record type name to find an existing one: use the ID in the load ID map, and flag duplicate names in the plan."),
        ],
        "also_record": ["The status code and errorCode of any refusal (write them into the findings).", "Whether letter case matters (step 4)."],
        "cleanup": [("DELETE", "/v2/record-types/{{rt_a_dup_id}}"), ("DELETE", "/v2/record-types/{{rt_a_lower_id}}"), ("DELETE", "/v2/record-types/{{rt_b2_id}}"),
                    ("DELETE", "/v2/record-types/{{rt_a_id}}"), ("DELETE", "/v2/workspaces/{{workspace2_id}}")],
    },
    {
        "id": 2, "key": "patch-on-fields", "title": "PATCH on fields",
        "purpose": "Settles whether a field can be updated in place, so its ID survives later changes (the load ID map is keyed on it). The v2 reference says PATCH uses merge-patch semantics, so only the properties sent should change; this checks it.",
        "before": ["rt_a_id is set (test 1 step 2), or run step 1 here to create one."],
        "steps": [
            _step("Create a throwaway record type (skip if rt_a_id is set)", "POST", "/v2/workspaces/{{workspace_id}}/record-types", {"displayName": PREFIX + " Patch"}, "rt_a_id", optional=True,
                  expect="201. The response id is saved as rt_a_id."),
            _step("Create a text field", "POST", "/v2/record-types/{{rt_a_id}}/fields", {"displayName": PREFIX + " Patch Me", "type": "text", "description": "before"}, "field_id",
                  expect="201. The response id is saved as field_id."),
            _step("Read the field and keep a copy of the response", "GET", "/v2/fields/{{field_id}}", expect="200. Note id, displayName, type, description and updatedAt."),
            _step("PATCH only the description", "PATCH", "/v2/fields/{{field_id}}", {"description": "after"}, expect="200."),
            _step("Read the field again and compare with step 3", "GET", "/v2/fields/{{field_id}}", expect="Compare every property with step 3."),
            _step("PATCH only the display name", "PATCH", "/v2/fields/{{field_id}}", {"displayName": PREFIX + " Patch Me Renamed"}, expect="200, or an error (note it).", optional=True),
            _step("PATCH the description to null, then read the field", "PATCH", "/v2/fields/{{field_id}}", {"description": None}, expect="200. A GET afterwards shows whether the description is cleared.", optional=True),
        ],
        "results": [
            ("After step 4 only the description changed (and updatedAt); id, displayName, type and options are unchanged", "PASS. A field can be updated in place and its ID survives. The schema builder can PATCH a description, for example to add or fix the Fusion marker."),
            ("The call is refused, or other properties changed or went missing", "FAIL. Do not update fields in place. A change would mean a new field, so the ID map must be able to replace an ID."),
        ],
        "also_record": ["Whether renaming (step 6) keeps the same ID.", "Whether null clears the description (step 7)."],
        "cleanup": [("DELETE", "/v2/fields/{{field_id}}"), ("DELETE", "/v2/record-types/{{rt_a_id}}")],
    },
    {
        "id": 3, "key": "description-on-create", "title": "Description on create",
        "purpose": "Settles whether the Fusion-driven marker can be written when a field is created, and records the exact property name and the length limit. The v2 reference lists description (maximum 512 characters) on a field; this confirms it works and is enforced.",
        "before": ["rt_a_id is set, or run step 1 here to create one."],
        "steps": [
            _step("Create a throwaway record type (skip if rt_a_id is set)", "POST", "/v2/workspaces/{{workspace_id}}/record-types", {"displayName": PREFIX + " Description"}, "rt_a_id", optional=True,
                  expect="201. The response id is saved as rt_a_id."),
            _step("Create a field with the marker description", "POST", "/v2/record-types/{{rt_a_id}}/fields", {"displayName": PREFIX + " Described", "type": "text", "description": MARKER_EXAMPLE}, "field_id",
                  expect="201. The response id is saved as field_id."),
            _step("Read the field", "GET", "/v2/fields/{{field_id}}", expect="The description comes back exactly as sent, including the quotes. Note the exact property name."),
            _step("Create a field with a 512-character description", "POST", "/v2/record-types/{{rt_a_id}}/fields", {"displayName": PREFIX + " Long 512", "type": "text", "description": "{{long_description_512}}"},
                  expect="201."),
            _step("Create a field with a 513-character description", "POST", "/v2/record-types/{{rt_a_id}}/fields", {"displayName": PREFIX + " Long 513", "type": "text", "description": "{{long_description_513}}"},
                  expect="400 if the limit is enforced (note the errorCode); 201 if it is not."),
            _step("Create a field with a 1,024-character description", "POST", "/v2/record-types/{{rt_a_id}}/fields", {"displayName": PREFIX + " Long 1024", "type": "text", "description": "{{long_description_1024}}"},
                  expect="201 means the limit is above 512 (the v3 PATCH documentation quotes 1,024, but that wording describes a record type's description, so it must be proven for a field).", optional=True),
            _step("Create a field with a 1,025-character description", "POST", "/v2/record-types/{{rt_a_id}}/fields", {"displayName": PREFIX + " Long 1025", "type": "text", "description": "{{long_description_1025}}"},
                  expect="400 means 1,024 is the real maximum; 201 means there is no limit this low.", optional=True),
        ],
        "results": [
            ("Step 3 returns the description exactly as sent, and step 5 is refused", "PASS. The marker can be written on create. The limit is 512 (the v2 reference), so the generated description (marker first) must be checked against 512 before it is sent. If steps 6 and 7 show 1,024 is allowed, raise the limit to 1,024."),
            ("Step 3 returns the description exactly as sent and step 5 succeeds", "PASS for the marker. The 512 limit is documented but not enforced: still keep descriptions under it."),
            ("The description is missing or changed in step 3", "FAIL. The marker cannot be written on create. Write it with a PATCH afterwards (test 2), or find the right property name."),
        ],
        "also_record": ["The exact property name that comes back (the reference says description).", "Whether the quotes in the marker survive."],
        "cleanup": [("DELETE", "/v2/record-types/{{rt_a_id}}")],
    },
    {
        "id": 4, "key": "reference-field-back-reference", "title": "Reference field with back reference",
        "purpose": "Settles what the corresponding-field setting (createBackReference) creates on the other record type, and whether it uses one of the 30 reference fields allowed per record type on both sides. This decides how many link fields a hub table can have.",
        "before": ["rt_a_id is set (earlier test), or run step 1 here.", "The field limits in the reference are 30 reference fields per record type."],
        "steps": [
            _step("Create the first throwaway record type (skip if rt_a_id is set)", "POST", "/v2/workspaces/{{workspace_id}}/record-types", {"displayName": PREFIX + " Link A"}, "rt_a_id", optional=True,
                  expect="201. The response id is saved as rt_a_id."),
            _step("Create the second throwaway record type", "POST", "/v2/workspaces/{{workspace_id}}/record-types", {"displayName": PREFIX + " Link B"}, "rt_b_id",
                  expect="201. The response id is saved as rt_b_id."),
            _step("Read the fields of both record types and count the ones of type reference (before)", "GET", "/v2/record-types/{{rt_a_id}}/fields", note="Do the same for record type B: GET /v2/record-types/{{rt_b_id}}/fields.",
                  expect="Write down the number of reference fields on each side (probably 0 and 0)."),
            _step("Create a reference field on A with the back reference ON", "POST", "/v2/record-types/{{rt_a_id}}/fields",
                  {"displayName": PREFIX + " Link With Back", "type": "reference", "referenceOptions": {"recordTypeId": "{{rt_b_id}}", "multiple": True, "createBackReference": True}}, "field_id",
                  expect="201. Note whether the response includes a backField."),
            _step("Read the fields of both record types again (after)", "GET", "/v2/record-types/{{rt_b_id}}/fields", note="And again for A: GET /v2/record-types/{{rt_a_id}}/fields.",
                  expect="Does a matching field appear on B? Did the reference count rise on A, on B, or on both?"),
            _step("Create a second reference field on A with the back reference OFF (the control)", "POST", "/v2/record-types/{{rt_a_id}}/fields",
                  {"displayName": PREFIX + " Link No Back", "type": "reference", "referenceOptions": {"recordTypeId": "{{rt_b_id}}", "multiple": True, "createBackReference": False}},
                  expect="201. Read both record types' fields again and compare with the counts after step 4."),
        ],
        "results": [
            ("A matching field appears on B and the reference count rises by one on BOTH record types", "A corresponding field uses a slot on each side. Count both sides toward the 30 when checking a hub table, and plan one back field per pair."),
            ("A matching field appears on B but only A's count rises", "The back field exists but is not counted toward B's 30."),
            ("No field appears on B", "createBackReference does not create a field here. Each side of a pair must be created as its own reference field."),
        ],
        "also_record": ["The name Workfront gives the back field.", "Whether the control field (back reference off) leaves B unchanged."],
        "cleanup": [("DELETE", "/v2/record-types/{{rt_b_id}}"), ("DELETE", "/v2/record-types/{{rt_a_id}}")],
    },
    {
        "id": 5, "key": "bulk-create-id-order", "title": "Bulk create ID order",
        "purpose": "Settles whether the IDs returned by a bulk create line up with the order the records were sent. The record ID map relies on it: if they do not line up, each record has to be matched by a distinguishing value instead of by position.",
        "before": ["rt_a_id is set (earlier test), or run step 1 here.", "A bulk create takes at most 100 records per call (documented)."],
        "steps": [
            _step("Create a throwaway record type (skip if rt_a_id is set)", "POST", "/v2/workspaces/{{workspace_id}}/record-types", {"displayName": PREFIX + " Bulk"}, "rt_a_id", optional=True,
                  expect="201. The response id is saved as rt_a_id."),
            _step("Read the record type to get its primary field", "GET", "/v2/record-types/{{rt_a_id}}", save="primary_field_id", expect="200. The primaryFieldId is saved as primary_field_id (or copy it by hand into the variable)."),
            _step("Bulk create five records, each with a distinct value", "POST", "/v2/record-types/{{rt_a_id}}/records/bulk",
                  [{"data": {"{{primary_field_id}}": PREFIX + " 1"}}, {"data": {"{{primary_field_id}}": PREFIX + " 2"}}, {"data": {"{{primary_field_id}}": PREFIX + " 3"}},
                   {"data": {"{{primary_field_id}}": PREFIX + " 4"}}, {"data": {"{{primary_field_id}}": PREFIX + " 5"}}],
                  expect="201 with results[], each with requestIndex and id. Copy the five (requestIndex, id) pairs."),
            _step("Read the records back", "GET", "/v2/record-types/{{rt_a_id}}/records?limit=100", expect="Find which id has which value, and compare with step 3."),
            _step("Bulk create three records where the MIDDLE one is invalid", "POST", "/v2/record-types/{{rt_a_id}}/records/bulk",
                  [{"data": {"{{primary_field_id}}": PREFIX + " 6"}}, {"data": {"fldDoesNotExist": PREFIX + " 7"}}, {"data": {"{{primary_field_id}}": PREFIX + " 8"}}],
                  expect="207 (mixed) with one failed result. Note the requestIndex of the failure and of the two that succeeded.", optional=True),
        ],
        "results": [
            ("In step 3 the result with requestIndex n has the id of the record whose value is n, and the results come back in send order", "PASS. IDs can be matched by position or by requestIndex. The ID map may use requestIndex (preferred) and still verify with a read-back."),
            ("The requestIndex values are right but the results are in a different order", "Match by requestIndex, never by list position."),
            ("The ids do not line up with the values", "FAIL. Do not trust position or requestIndex: match each record by a distinguishing value read back after the create."),
        ],
        "also_record": ["Whether a failed record in step 5 keeps the requestIndex of its place (so the others can still be matched).", "The HTTP status of a partly failed bulk call."],
        "cleanup": [("DELETE", "/v2/record-types/{{rt_a_id}}")],
    },
]

ORDER_NOTE = ("Run the tests in order. Each later test reuses the throwaway record type (rt_a_id) from the first one, or creates its own if it is not set. "
              "If a step fails with a permission error, stop and note the status and errorCode: that is a finding too.")
RULES = [
    "Use a DEV Workspace only, never a Workspace with real records.",
    "Every name the tests create starts with " + PREFIX + ". Delete everything you created when you finish (the cleanup list under each test).",
    "Deleting a record type also deletes its records, and a delete is permanent.",
    "You add your own authorization in Postman. The app never sees a token and never calls Workfront for these tests.",
    "Anything the reference does not document is marked as something the test finds out; write down what you see, including error codes.",
]


def guide():
    """The whole guide as plain data (what the page shows)."""
    return {"base_url": BASE_URL, "prefix": PREFIX, "order_note": ORDER_NOTE, "rules": RULES, "variables": [{"name": n, "value": v, "help": h} for n, v, h in VARIABLES], "tests": TESTS,
            "source": "Saved Workfront Planning API v2 reference (wf-planning-api-v2-simplified/1.0). Authentication is not documented there; confirm it in Adobe Developer Console or the Workfront documentation."}


def _url(path):
    clean, _, query = path.partition("?")
    segs = [s for s in clean.split("/") if s]
    u = {"raw": "https://{{customer_domain}}/maestro/api" + path, "protocol": "https", "host": ["{{customer_domain}}"], "path": ["maestro", "api"] + segs}
    if query:
        u["query"] = [{"key": kv.partition("=")[0], "value": kv.partition("=")[2]} for kv in query.split("&")]
    return u


def _script(var):
    return ["// saves the new ID for the next steps (only when the call worked)", "if (pm.response.code >= 200 && pm.response.code < 300) {",
            "  var j = pm.response.json();", "  var v = j && %s;" % ("j.primaryFieldId" if var == "primary_field_id" else "j.id"),
            "  if (v) { pm.collectionVariables.set(\"%s\", v); }" % var, "}"]


def collection():
    """A Postman collection (v2.1) with the same requests, using collection variables. It has NO authorization: add yours in Postman."""
    items = []
    for t in TESTS:
        reqs = []
        for k, s in enumerate(t["steps"], 1):
            r = {"method": s["method"], "header": [{"key": "Content-Type", "value": "application/json"}] if s["body"] is not None or s["method"] in ("POST", "PUT", "PATCH") else [], "url": _url(s["path"])}
            if s["body"] is not None or s["method"] in ("PATCH",):
                r["body"] = {"mode": "raw", "raw": json.dumps(s["body"], indent=2), "options": {"raw": {"language": "json"}}}
            desc = (s["expect"] or "") + ((" " + s["note"]) if s["note"] else "") + (" (optional)" if s["optional"] else "")
            item = {"name": "%d.%d %s" % (t["id"], k, s["label"]), "request": dict(r, description=desc)}
            if s["save"]:
                item["event"] = [{"listen": "test", "script": {"type": "text/javascript", "exec": _script(s["save"])}}]
            reqs.append(item)
        clean = [{"name": "%d.C%d Cleanup: %s" % (t["id"], k, p), "request": {"method": m, "header": [], "url": _url(p), "description": "Deletes what the test created. A delete is permanent."}}
                 for k, (m, p) in enumerate(t["cleanup"], 1)]
        items.append({"name": "Test %d: %s" % (t["id"], t["title"]), "description": t["purpose"], "item": reqs + clean})
    return {"info": {"name": "Workfront Planning v2: migration design tests (DEV only)", "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
                     "description": "Five throwaway tests for a DEV Workspace. No authorization is stored here: add your own. Set customer_domain and workspace_id first. " + ORDER_NOTE},
            "variable": [{"key": n, "value": v, "description": h} for n, v, h in VARIABLES], "item": items}
