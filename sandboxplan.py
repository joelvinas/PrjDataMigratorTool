#!/usr/bin/env python3
"""The sandbox test plan: behaviors that are undocumented or unconfirmed and must be proven once in a sandbox before the loader relies on them. Standard library only.

Planning items that a Postman test already covers point to it. The Core and Airtable items are done by hand in the product: no Core request reference is held, so no
Core request is shown. Nothing here is recorded in the database; the outcome goes into the project notes.
"""
NOTE = ("Each item is an undocumented or unconfirmed behavior to prove once in a sandbox before the loader relies on it. "
        "Nothing here is recorded in the database: write the outcome into the project notes.")

ITEMS = [
    {"id": "S1", "title": "Field description length limit", "system": "Planning and Core", "how": "postman", "covered_by": "Postman test 3",
     "why": "The repo applies no description limit because Adobe documents none for Core; the Planning reference says 512. The Fusion marker and the lineage text must fit.",
     "steps": ["Planning: run Postman test 3 (512 and 513 characters).",
               "Core: in a sandbox, create a custom field and paste descriptions of 512, 513, 1,000 and 4,000 characters; note where it stops accepting or truncates."],
     "result": "Write down the largest description each system keeps whole. Set DESCRIPTION_MAX_LEN in fielddesc.py to the smaller one once confirmed.",
     "decides": "Whether and where to cap generated descriptions."},
    {"id": "S2", "title": "Write-once calculated field in Core, once per object type", "system": "Core", "how": "manual", "covered_by": None,
     "why": "The design stores a value once and lets a calculation return the native value while the field is blank and the stored value afterwards. It has not been proven, and it must work for each object type (project, request, task).",
     "steps": ["In a Core sandbox, create a custom field to hold the stored value and a calculated field that returns the native value when the stored one is blank, otherwise the stored value.",
               "Create an object of each type (project, request, task). Check the calculated field shows the native value.",
               "Push a stored value once, through the API or a Fusion scenario. Check the calculated field now shows the stored value.",
               "Change the native value. Check the stored value still wins. Try to push a second stored value and note what happens."],
     "result": "Pass for an object type if the calculated field shows native, then stored, and the stored value survives a native change. Record any type where it does not.",
     "decides": "Whether the Core to Planning publish pattern (step 4e) can rely on it."},
    {"id": "S3", "title": "Can a Planning formula output a clickable link?", "system": "Planning", "how": "manual", "covered_by": None,
     "why": "The design stores the Core object link as plain text and does not depend on this. Test it only if that route is wanted.",
     "steps": ["In a DEV Workspace, create a formula field whose formula builds a web address from a text field.", "Open a record and check whether the result is clickable, plain text or an error."],
     "result": "Clickable means a formula could replace the stored link; anything else changes nothing.", "decides": "Optional: whether the link field can be calculated."},
    {"id": "S4", "title": "Select options: add, rename, reorder, and IDs on create", "system": "Planning and Core", "how": "postman_partly", "covered_by": "extends Postman test 2",
     "why": "Whether options can change after creation decides how a changed Airtable option list is applied, and whether option IDs come back on create decides how the option map is filled.",
     "steps": ["Planning: create a single-select field with three options and keep the response. Check whether each option has an ID in it.",
               "PATCH the field with a changed option list (a new option, a renamed option, a different order) and read it back; note which changes were kept and whether the IDs survived.",
               "Core: in a sandbox, add, rename and reorder the options of a custom field in the product and check whether the option IDs stay the same."],
     "result": "Write down, per system: IDs on create (yes or no), add, rename and reorder after creation (yes or no), and whether a rename keeps the option's ID.",
     "decides": "How option changes are applied and how the option ID map is filled."},
    {"id": "S5", "title": "The 500-field limits against the largest table", "system": "Planning and Core", "how": "manual", "covered_by": "the plan's limit check",
     "why": "A Planning record type holds up to 500 fields and a Core custom form up to 500 (performance slows above about 100). The largest table must fit.",
     "steps": ["Read the largest table's field count from the plan summary (Planning limits).",
               "If it is over 100, build a throwaway form or record type with that many fields in a sandbox and open it; note how it performs."],
     "result": "Pass if it opens and edits acceptably; otherwise the table needs a split or fewer fields.", "decides": "Whether the biggest table must be split before loading."},
    {"id": "S6", "title": "Core custom field creation (the PARAM call)", "system": "Core", "how": "manual", "covered_by": None,
     "why": "The request shape, required fields and API version of the Core create call are not in the saved references, so no Core request is built. This is the main thing blocking a real Core run.",
     "steps": ["In Workfront's API explorer for the sandbox (or the Core API reference for your version), find the create call for a custom field (PARAM) and for attaching it to a form (CTGYPA).",
               "Create one field and attach it. Save the exact request and response (with any token removed) as the source for the Core transport."],
     "result": "A working request for each, with its required properties and the API version.", "decides": "The shape the Core transport uses."},
    {"id": "S7", "title": "Airtable attachment links: how long they live", "system": "Airtable", "how": "manual", "covered_by": None,
     "why": "The design fetches a fresh link for each file just before download because links expire in roughly two hours. That figure is unconfirmed.",
     "steps": ["Pull one record with an attachment from a throwaway base and note the time. Request the link again every 30 minutes until it stops working."],
     "result": "The time a link stays valid, and whether the same link is returned on a repeat call.", "decides": "How the attachment mover refreshes links."},
]


def plan():
    return {"items": ITEMS, "note": NOTE}
