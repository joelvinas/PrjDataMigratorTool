# CLAUDE.md: standing rules for this repo

Read README.md first. These rules apply to every change, every session, and any AI assistant working here. Where a request conflicts with a rule below, say so and ask before proceeding.

## What this is

A local, standard-library-only Python web app (`server.py`, plain HTML/CSS/JS, SQLite) that controls the migration of two Airtable bases into Adobe Workfront: Workfront Core (custom fields, custom forms, projects, requests, tasks) and Workfront Planning (record types). Stage 1 ingests the Airtable schema and profiles the data; Stages 2 and 3 are manual recorders for forms and automations. The field name validator (`fieldnames.py`, `fieldnames_io.py`) checks the renamed field and table names against a documented rubric.

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
- Match the existing style of nearby code; keep rules as small, separate, testable functions or data.

## Testing

- Run `python test/run_regression.py` after every change (a single suite: `python test/run_regression.py names`). All suites must pass before a change is called done.
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
- Comparing field values reads the saved record file in memory and returns counts and record IDs only; a value never goes into a response, the database or a log.
- Field descriptions use the lineage syntax `Source: Airtable. Origins: Table.Field; Table.Field.` with the ORIGINAL Airtable names; they are generated each time and only hand edits are saved. No description length limit is applied until one is confirmed.
- Every in-scope table name must be approved before any field is reviewed. A table with no scope decision is undecided (validated, badged), which is different from out (skipped).
- The three names can be configured separately (per-name step toggles, length, leading-digit permission and preset, extra allowed or removed punctuation); the defaults make them identical, and Planning's length can never exceed Adobe's documented 250.
- Limits not confirmed by Adobe documentation (Core name and label length) are marked provisional wherever they appear.
