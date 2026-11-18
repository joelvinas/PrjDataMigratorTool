# CLAUDE.md: standing rules for this repo

Read README.md first. These rules apply to every change, every session, and any AI assistant working here. Where a request conflicts with a rule below, say so and ask before proceeding.

## What this is

A local, standard-library-only Python web app (`server.py`, plain HTML/CSS/JS, SQLite) that controls the migration of two Airtable bases into Adobe Workfront: Workfront Core (custom fields, custom forms, projects, requests, tasks) and Workfront Planning (record types). Stage 1 ingests the Airtable schema and profiles the data; Stages 2 and 3 are manual recorders for forms and automations. The field name validator (`fieldnames.py`, `fieldnames_io.py`) checks the renamed field and table names against a documented rubric.

## Layout

Every Python module lives in `src/` (run the app with `python src/server.py`, the loader with `python src/loader.py`). The pages live in `web/` (`web/pages/*.html`, `web/js/*.js`, `web/css/style.css`; the URLs stay flat, e.g. `/stage1.js`, and `STATIC` in `server.py` maps each one to its file). `config.json`, `db/`, `data_extract/`, `sql/`, `docs/` and `test/` stay at the project root. `server.py` finds the project root from its own location, so new modules go in `src/` and tests add `src/` to their import path. Do not add loose `.py`, `.html`, `.js` or `.css` files to the root; a new page goes in `web/` and gets a `STATIC` entry.

**Keep the root tidy.** Scratch files never go in the root: no `*.db`, logs, CSVs or exports. Throwaway files go in `tmp/` (git-ignored, safe to delete at any time); a one-off script's own output goes in `out/`. A hand-run test against a live server needs a database path that is relative to the project root, so use `tmp/name.db` (create `tmp/` first; the app only auto-creates `db/`), never a bare `name.db`. Delete what you create there when you are done.

## Standing rules

- **Standard library only.** No new dependencies, no build step. Playwright is allowed in the optional browser tests only.
- **Schema changes are additive and approved first.** Never rename or drop an existing table or column. Flag any schema change, explain it, and wait for approval. The owner applies approved `CREATE TABLE` statements against the live database themselves, so provide the DDL in the style of `sql/DatabaseSchemaLibrary.sql` plus an additive, repeatable `sql/migration_NNN_*.sql`. Do not add a new table to `DatabaseSchemaLibrary.sql`, the DDL embedded in `server.py`, or `sql/apply_migrations.py` until the owner confirms the migration is applied: a test requires the library file to equal the embedded DDL, and the app refuses a database that lacks an expected table.
- **Secrets.** The Airtable token never appears in the database, logs, error messages or any response sent to the browser. `config.json` and database files are never committed.
- **Record values never go into SQLite, logs, reports or the browser.** Field and table metadata and aggregate counts are fine.
- **Deterministic.** Same input, same output. No LLM, model call or randomness at runtime. Sort anything that is iterated into output.
- **Public repository.** No secrets and no real client data anywhere: not in code, tests, fixtures, comments, docs or commit messages. Test names are fabricated.
- **Human decisions are recorded, never automatic.** Anything a person decides (approve, abandon, scope) is stored with who, when and why. The tool proposes; a person approves. Nothing renames a field or table on its own.
- **Every Airtable API call** goes through `api_call` in `server.py` so it is logged.

## Writing conventions

- Say **allowlist** and **blocklist**, never whitelist or blacklist.
- Docs and report text are plain language for a client audience.
- **Long grids sit in a scrolling window** (`class="tablewin"`, sticky headings). Every window must stay keyboard-focusable (`web/js/common.js` does this): the owner pans and scrolls with the arrow keys, Page Up/Down, Home and End, and that must not be lost. A browser test covers it.
- Match the existing style of nearby code; keep rules as small, separate, testable functions or data.

## Testing

- Run `python test/run_regression.py` after every change (a single suite: `python test/run_regression.py names`). All suites must pass before a change is called done.
- **Test databases never go in the root or in `db/`.** `test/run_regression.py` runs every suite in a throwaway copy of the project under the system temp folder (it skips `*.db`, `tmp/`, `db/`, `config.json` and `data_extract/`) and deletes it afterwards, so a suite can name its database freely (`"v.db"`) and it lands in that copy. Suites that make their own files use `tempfile.mkdtemp(prefix=...)`. Run a suite by hand only with the runner; if you must run one script against your own server, point it at `tmp/`. After a test run, `git status --short` and `ls *.db` in the root must show nothing new.
- New behavior gets tests with fabricated data. Report real failures plainly; never describe a failing or skipped check as passing.

## Commits (this is where drafts go wrong, so follow it exactly)

The owner has authorised commits to the local repository: commit at the end of each stage of work that changes files, one logical change per commit. The owner pushes to the remote from SourceTree; never push, force-push, rewrite published history or touch another branch's work without being asked. Do not commit on `main`; use a feature branch. Stage specific files by name (never `git add -A`), and never commit `config.json`, `*.db`, `data_extract/` or `out/`. Never skip hooks or signing.

A commit is one logical change. If the work has two unrelated parts, make two commits. Run the regression suite first; do not commit a red tree.

Message format:

```
<Imperative subject, 72 characters or fewer, no trailing period>

<Body: what changed and WHY, wrapped at about 72 columns. Mention any rule
or decision that shaped it (for example "Adobe documents ( ) as unsupported
in labels"). Mention anything deliberately left out or still provisional.>

Co-Authored-By: <exactly the attribution line the session provides>
```

- The subject says what the change does, not what files it touches ("Gate field review on table approval", not "Update fieldnames.py").
- The body explains the reason and the effect, including behavior changes a reviewer would not guess from the diff, and any schema change or migration needing the owner's action.
- Facts only: counts of passing checks must be real, and limits that are unconfirmed are called provisional.
- Always end with the `Co-Authored-By` line the session supplies. Add a `Claude-Session` trailer only if the environment provides one; never invent one.
- Before each commit, show the owner the message. After committing, show `git log -1 --stat` so the result is visible.

## Field name validator: decisions in force

- Three targets are validated separately: Planning name, Core name, Core label.
- AUTO_FIXABLE means only: deleting the characters `' " \` ~ ! @ # $ % ^ & * ( ) [ ] { } ; : < > , . / ?` (nothing in their place), turning whitespace characters (ASCII 160, tab, new line, carriage return, form feed, vertical tab) into a plain space, closing repeated and edge spaces, and swapping a renamed table's name when the schema identifies the table and its new name is approved. Everything else is NEEDS_HUMAN with a proposed repair. Auto-fixable batches may be approved together; nothing is ever applied without approval.
- Names are ASCII-safe (letters, digits, space, hyphen), may not start with a digit, and are unique within a table. Labels keep the original text except Adobe's unsupported characters and emoji.
- Emoji: each name chooses remove (default) or replace with its Unicode name; descriptions have their own choice. An emoji is never silently kept in a name.
- The loader (Stage 4) is built in steps: the plan and dry run exist and send nothing; `run --send` now creates record types, fields and select options through the two transports (`coretransport.py`, `planningtransport.py`, over `workfronthttp.py`), using the facts the owner verified on 17 November 2026; the assumptions still unverified are listed in docs/LOADER_PLAN.md and in the README, and nothing has been run against a real Workfront. Attaching Core fields to a form is manual. Links are the second pass. Every setting and secret comes from config.json, never from code, and a secret never reaches the database, a log, a report or the browser. Sandbox and dev are the defaults; a real Core production run needs the exact phrase CREATE IN PRODUCTION. Planning stages (dev, uat, production) are only names the user defines in config.json (workfront.planning, one block per stage with its own base_url, bearer_token and workspace_id; stored lower case, no schema change): never hardcode a Workspace or assume a stage exists. The Load a table page proves the chosen stage with one read of its Workspace and unlocks only on success; a real send to the production stage also needs CREATE IN PRODUCTION (DELETE RECORDS IN PRODUCTION for a reset). Migrations 010 to 019 (load_id_map with its workspace_key, table_core_objects, field_names.core_target_type, field_relationships with its direction columns, option_names, option_workfront_ids, and the record ID map: source_records, target_mappings, record_pass_markers, attachment_map) are applied and folded into the library, the embedded DDL and apply_migrations.py. The runner state (loadstate.py, load_id_map), the link directions and the record registry (recordmap.py: IDs, statuses, sizes and hashes only, never a value, file name or link) use them so far. Migration 018 (link_direction_events, an append-only history of link direction decisions) and 019 (field_writer_decisions and field_writer_events: a person's decision about a formula-only or automation-only field, with an append-only history) are applied and folded in. Select option names follow the label rules; derived fields stay on hold. Workfront credentials live only in config.json (section workfront, read by loadconfig.py), never in the database, logs, reports or browser. See docs/LOADER_PLAN.md.
- Link directions (Stage 1, step 4; `linkdirections.py`): the tool detects pairs and suggests a direction; a person confirms each one (who, when, why on the `field_relationships` row). Choosing on one field auto-sets its partner. One to one and many to many take a tiebreaker; automatic pairs are non-binding and anything the tool had to order, or that loops, is flagged. The load order is provisional, and a link field is blocked in the load plan, until every pair is decided.
- Field writers (Stage 3, section E; `fieldwriters.py`): who fills each field is COMPUTED each time from the latest saved automations and forms, never stored. A field carries the Fusion-driven marker, first in its description, if and only if a recorded automation SETS it. A person decides what happens to formula-only fields (recompute or park) and automation-only fields (Fusion-owned, go stale, or people also edit it); who, when and why are stored. A person typing is only ever 'possible', since the specs cannot show it. The matrix is validated against descriptions pulled from Workfront (an uploaded CSV or JSON) in both directions; nothing is stored and no description text is sent back.
- Data types (Data types page, `datatypes.py`, `datatypes_store.py`): Planning and Core have different type lists, so each field has two answers. Planning is `field_names.target_type` (the column is not renamed; the view `field_data_types` calls it `planning_target_type`) and must be one of Adobe's 17 types. Core is `core_target_type` (the dataType) plus `core_display_type` (the displayType), two columns because the API needs both. A value off its list is never stored or used: the importer drops it with a warning, and a stored one shows as invalid until a person clears it (the old value stays in the history). A default mapping per Airtable type is proposed and a person applies it, overrides it or sets many fields at once; who, when and why are recorded (`field_type_decisions`, `data_type_events`). The plan sends each system its own type and blocks a field per system when its type is missing. Migrations 021 and 022 are drafts: do not fold them into the library, the embedded DDL or `apply_migrations.py` until the owner confirms they are applied (tests apply them with `test/regression/typesfixture.py`).
- Planning limits and the Core history size (`planlimits.py`) are checked in the load plan and reported as warnings for a person; they never change an item's status on their own.
- Postman test guide (`/postman`, `postmantests.py`): static text and a collection for tests the owner runs by hand in a DEV Workspace. The app never calls Workfront for them and never holds a token; the collection has no authorization.
- Consolidation groups (several fields of one table become one) use migration 009 (approved and folded into the schema). The conflict rule defaults to 'review': nothing is decided silently.
- Comparing field values reads the saved record file in memory and returns counts and record IDs only; a value never goes into a response, the database or a log.
- Field descriptions use the lineage syntax `Source: Airtable. Origins: Table.Field; Table.Field.` with the ORIGINAL Airtable names; they are generated each time and only hand edits are saved. A field a recorded automation sets starts with the Fusion-driven marker (`Fusion-driven: written by scenario "X".`), added each time and never stored, so a hand edit can neither remove nor fake it. No description length limit is applied until one is confirmed (the marker goes first so a long lineage cannot push it out).
- Every in-scope table name must be approved before any field is reviewed. A table with no scope decision is undecided (validated, badged), which is different from out (skipped).
- The three names can be configured separately (per-name step toggles, length, leading-digit permission and preset, extra allowed or removed punctuation); the defaults make them identical, and Planning's length can never exceed Adobe's documented 250.
- Limits not confirmed by Adobe documentation (Core name and label length) are marked provisional wherever they appear.
