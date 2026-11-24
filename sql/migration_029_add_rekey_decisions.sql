-- Migration 029 (applied by the owner and folded into the schema library, the embedded DDL and apply_migrations.py on 24 November 2026): the decisions of the re-key step. Adds ONE table. Additive only, safe to run more than once. Touches no existing table.
-- Holds who decided, when and why, and for each record type what happened: IDs, an outcome and a short scrubbed reason. No record value and no token.
--
-- Why: the re-key page (/rekey) makes the field that was the primary field in Airtable the primary field of each record type in the dev Workspace, after the data is validated.
-- That is a person's decision, so it is recorded like every other decision in this tool (who, when, why). The page already writes the same facts to a report; this table keeps them
-- in the database too, one row per record type per run, and is only ever added to (a row is never changed or deleted by the tool). Until the owner applies it, the page skips this
-- write, says so, and the report is unaffected (a database without the table). Approved by the owner on 24 November 2026.
--
-- A new database has it from the library; apply_migrations.py runs this file through src/draftmigrations.py for an older database.

CREATE TABLE IF NOT EXISTS rekey_decisions (
    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,                  -- one re-key run: R<UTC start as yyyymmddThhmmssZ>-<Airtable base ID>
    decided_at TIMESTAMP NOT NULL,         -- when the run started (UTC)
    decided_by TEXT NOT NULL,              -- the person's initials or short name, as typed on the page
    reason TEXT NOT NULL,                  -- why, in the person's words (plain text, at most 500 characters)
    base_id TEXT NOT NULL,
    environment TEXT NOT NULL,             -- the Planning stage (dev)
    workspace_key TEXT NOT NULL,           -- the Planning Workspace ID
    table_id TEXT NOT NULL,                -- the Airtable table
    record_type_id TEXT,                   -- the Planning record type
    airtable_field_id TEXT,                -- the field that was the primary field in Airtable
    planning_field_id TEXT,                -- the Planning field it became, which was made the primary field
    previous_primary_field_id TEXT,        -- the primary field the record type had before (AirtableRecordId after a Git-R-Done run)
    outcome TEXT NOT NULL CHECK(outcome IN ('rekeyed', 'skipped', 'failed')),
    detail TEXT,                           -- a short message with any credential removed: why it was skipped or failed
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (run_id, table_id)
);
CREATE INDEX IF NOT EXISTS idx_rekey_decisions_table ON rekey_decisions(base_id, table_id, decided_at);
