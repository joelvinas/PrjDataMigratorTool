# Loader plan (Stage 4: Map and load)

Status: **begun.** The load plan, the dry run and the runner's rules are built and tested. Nothing is created in Workfront yet, deliberately: the request shapes are not verified and no credentials are configured (see "What is not verified").

## What the Loader is

Stage 4 takes the approved names, the scope and target decisions, the consolidations and the saved records, and creates the target in Workfront: first the structure (record types, fields, select options), then the records. It is built in steps, each safe to stop after.

| Step | What | Status |
| --- | --- | --- |
| 4a | **The load plan and the dry run**: everything that would be created, with temporary IDs and a status; the runner's rules (sandbox by default, idempotent, one failure never stops a run) | **Built** |
| 4b | **Field creation in the sandbox**: real Planning and Core transports (separate modules, each with its own authentication), the ID map table, the crosswalk written back, every call logged against its temporary ID | Waiting on the owner applying migrations 010 to 013 and on the request-shape checks below |
| 4c | **Record load order and the record ID map**: Airtable record ID to Core object ID and Planning record ID, so links and attachments can be reattached | Not started; needs a schema proposal |
| 4d | **Attachments** as native Core documents (link expiry, download behavior against real Airtable is untested) | Not started |
| 4e | **The Core to Planning publish pattern**: the write-once Planning ID field, the sync status, idempotent publish, the reconciliation report | Not started; needs sandbox proof first |

## What is built (4a)

Run it read-only against the database. Nothing is sent anywhere.

```
python loader.py plan    --db db/migration.db --base appXXXXXXXXXXXXXX
python loader.py dry-run --db db/migration.db --base appXXXXXXXXXXXXXX --env sandbox
```

- **The plan** (`loadplan.py`) is an ordered list of items: Planning record types, Core custom forms, fields in each system, select options in Airtable's order, and the fields the migration adds so a Core object and its Planning record can find each other (Core object type, Core object ID, Core object link in Planning; Planning record ID and Sync status in Core). A Planning field needs its record type first, so the record type is its parent; a Core custom field exists on its own and names the form it will join later.
- **Temporary IDs** depend only on the Airtable IDs (`P-T-<table>`, `P-F-<field>`, `P-O-<field>-<choice>`, `C-F-<field>`, ...), so they are identical on every run. That is what makes a restart safe.
- **Each item has a status.** `ready`: everything is decided and approved. `hold`: a design decision is open (a derived field, a relationship, an attachment, the Core object type). `blocked`: something this tool controls is missing (table or name not approved, no target data type, scope or target undecided). The reasons are listed.
- **The runner** (`loadrun.py`) takes one transport per system. It defaults to **sandbox**; production needs the exact phrase `CREATE IN PRODUCTION` and only for a real run. It is **idempotent** (a Workfront ID is written back against the temporary ID at once, and anything that already has one is skipped), a **failure never stops the run** (the item is marked failed with a message from which any credential has been removed, and what depends on it is skipped), and **dry run is the default**.
- Only a dry-run transport exists. It reports the intent (name, type, parent), not a request body.

## What Adobe's documentation says (checked 14 November 2026)

**Confirmed**

- **Workfront Planning V2 has modules to create, update and delete record types and fields**, and to create and update records. The V1 connector has no create or update for record types or fields. ([Adobe Workfront Planning modules](https://experienceleague.adobe.com/en/docs/workfront-fusion/using/references/apps-and-their-modules/adobe-connectors/workfront-planning-modules))
- **Authentication is OAuth 2.0**: either Adobe Workfront auth (Client ID and secret) or a Server-to-Server connection. **A sandbox needs its own OAuth2 application** and credentials. Planning is not available for legacy packages, and the instance must be onboarded to the Unified Experience. (same page)
- **A Planning field name can be up to 250 characters; a record type can have up to 500 fields.** ([Field overview](https://experienceleague.adobe.com/en/docs/workfront/using/adobe-workfront-planning/adobe-workfront-planning-fields/fields-overview))
- **A Core custom field name (its "API name") must be unique across the whole Workfront instance**; Adobe advises against periods and against reusing built-in field names; the characters `\t \n \r \f [ ] ( ) : { }` are unsupported in labels and API names. ([Create a custom form](https://experienceleague.adobe.com/en/docs/workfront/using/administration-and-setup/customize/custom-forms/design-a-form/design-a-form))
- **A Core custom form holds at most 500 fields**, and performance can slow above about 100. ([Create a custom form (course)](https://experienceleague.adobe.com/en/courses/administer-and-maintain-adobe-workfront-i/capture-information-with-custom-forms/create-a-custom-form))

**Reported by the community, not confirmed by Adobe**

- Core custom fields are `PARAM` objects and custom forms are `CTGY` objects; a field is attached to a form by updating the form's `categoryParameters` (objCode `CTGYPA`). ([community thread](https://experienceleaguecommunities.adobe.com/adobe-workfront-general-23/api-call-attach-assign-existing-custom-field-parameter-to-existing-custom-form-category-127216))
- Planning: field IDs are not shown in the UI, fields cannot easily be found by name, and patching individual select options may be undocumented. The only reply in that thread is from a partner, not Adobe, and says V2 may cover it. ([thread, May to October 2026](https://experienceleaguecommunities.adobe.com/adobe-workfront-23/workfront-planning-fusion-api-limitations-250603))

## What is not verified (so not built)

1. The exact **request shapes** for creating a Planning record type, field and select option, and a Core custom field and its options. Adobe's published pages found do not give an endpoint reference for them; the Fusion modules show they exist.
2. Whether a create call **returns the option IDs**, or whether they must be read back, and whether **options can be added or reordered afterwards** (this decides how "every select field's options by ID, in Airtable order" is delivered).
3. Core's **PARAM creation call**, its required fields and the API version in your instance.
4. Credentials: whether you will use OAuth2 Server-to-Server for both systems, and where they live.

## Decisions made (14 November 2026)

1. **Core object type per table:** recorded in a new table, `table_core_objects` (project, request or task, with who, when and why). Proposed in [migration 011](../sql/migration_011_add_table_core_objects.sql); not applied. Until a table has a row, its Core form stays on hold.
2. **A data type per system:** a new column, `field_names.core_target_type`, beside the existing `target_type` (which stays the Planning type). Proposed in [migration 012](../sql/migration_012_add_core_target_type.sql); not applied. The importer does not read a Core type yet.
3. **Derived fields** (formulas, lookups, rollups, counts, automatic numbers, created and modified times): **on hold**. They stay on hold in the plan.
4. **Relationships:** defined by the Airtable schema and stored in the database, in `field_relationships` (one row per link field, its paired field, cardinality, which side is the real relationship, and who approved it). Proposed in [migration 013](../sql/migration_013_add_field_relationships.sql); not applied. Link fields stay on hold until it exists.
5. **Select option names follow the Core label rules** (built): they keep the original text except Adobe's unsupported characters, emoji and non-ASCII characters. An option name that breaks them is put on hold with the proposed repair; nothing renames on its own. Approving an option name is not stored yet.
6. **Migration 010 (`load_id_map`) is approved.** It is applied by the owner; it is folded into the schema library, the embedded DDL and `apply_migrations.py` only after the owner confirms it is applied.
7. **Credentials:** a Core sandbox exists. The Core credential and the Planning bearer token go in `config.json` (see `config.example.json`, section `workfront`: Core per environment, Planning once with its Workspace ID; `python loader.py config` says yes or no for each). They are never stored, logged or sent to the browser, and `loadconfig.redact()` removes them from messages. Nothing uses them yet.

**Planning has no environments** (owner, 14 November 2026): everything migrates into a Workspace treated as isolated for Dev. If the upload is fast, the Dev Workspace is wiped and a UAT Workspace created; if not, Dev is promoted to UAT. So Core has sandbox and production, and Planning has Workspaces. The Planning block of `config.json` is not per environment and carries the Workspace ID, and migration 010 now labels stages `sandbox`, `production`, `dev` and `uat` with a `workspace_id`. The loader never deletes: a wipe or a promotion is a person's recorded step.

Still open: how the runner's single environment maps to a Core stage plus a Planning Workspace stage, what confirmation a UAT or production Planning run needs, how an approved option name is stored ([migration 014](../sql/migration_014_add_option_names.sql) is one proposal, not authorised), and the request shapes below.

## The sandbox test plan (from the Migration Design prompt)

Each is an undocumented or unconfirmed behavior to prove once in a sandbox before the loader relies on it.

1. The **description length limit** for a field.
2. The **write-once calculated field** pattern in Core (the calculation returns the native value when the field is blank and the stored value otherwise, and the stored value is pushed once through the API or Fusion), **once per Core object type** (project, request, task).
3. Whether a **Planning formula can output a clickable link**. The design builds the Core object link as plain text in a single-line text field and does not rely on this; test it only if you want that route.
4. Whether **select options can be added, renamed and reordered** after creation, and whether their IDs come back at creation.
5. The **500-field limits** (Planning record type, Core form) against the largest table.

## Risks

- A record type delete **also deletes its records** ([Adobe](https://experienceleague.adobe.com/en/docs/workfront-fusion/using/references/apps-and-their-modules/adobe-connectors/workfront-planning-modules)); the loader will never delete.
- A Core custom field name must be unique across the instance, which the per-table check cannot see: the loader needs a check against the instance's existing names before creation.
- Production is a deliberate extra step: sandbox by default, an exact confirmation phrase for a real production run, separate IDs per environment.
- Nothing here has run against a real Workfront instance. Every statement above about the APIs comes from documentation, not from a call.
