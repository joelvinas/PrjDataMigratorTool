-- Migration 025 (DRAFT, not applied): the field results of an Emergency mode run. Adds ONE table. Additive only, safe to run more than once. Touches no existing table.
-- Holds field and table names, IDs, counts, option names, formula text, an outcome and a short error code and text. No record value and no token.
--
-- Why: the Emergency mode tab pushes one table into the dev Workspace and tries each field as its real Planning type, falling back to a PRE- text field when Planning refuses
-- it. This table keeps one row per field per run (what was tried, what happened, what the field became, how long its longest value is), so a later run, a fix and the load
-- of the links can start from facts instead of from a report. Until the owner applies it, the app skips this write and the reports are unaffected.
--
-- Not folded into DatabaseSchemaLibrary.sql, the embedded DDL in server.py or sql/apply_migrations.py until the owner confirms it is applied (a test requires the library
-- file to equal the embedded DDL, and the app refuses a database that lacks an expected table).

CREATE TABLE IF NOT EXISTS emergency_field_results (
    result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,                  -- one Go run: E<UTC start as yyyymmddThhmmssZ>-<Airtable table ID>
    run_at TIMESTAMP NOT NULL,             -- when the run started (UTC)
    base_id TEXT NOT NULL,
    table_id TEXT NOT NULL,
    airtable_field_id TEXT NOT NULL,
    airtable_field_name TEXT NOT NULL,
    airtable_type TEXT NOT NULL,
    planning_name TEXT,                    -- the Planning name the naming rules suggest (a suggestion for the crosswalk; the name SENT is the Airtable name, or PRE- + it)
    core_name TEXT,                        -- the suggested Core name
    label_name TEXT,                       -- the suggested Core label
    attempted_planning_type TEXT,          -- the Planning type tried first
    outcome TEXT NOT NULL CHECK(outcome IN ('clean', 'pre-converted', 'skipped', 'failed')),
    planning_field_id TEXT,                -- the Planning field ID (also in load_id_map, against the Airtable field ID)
    error_code TEXT,                       -- Planning's error code for the refused attempt
    error_text TEXT,                       -- a short message with any credential removed
    max_length INTEGER,                    -- the longest value, in characters (a number only; the values themselves are never kept)
    cardinality INTEGER,                   -- from the profile (airtable_fields.cardinality)
    null_count INTEGER,
    total_records INTEGER,
    option_list TEXT,                      -- JSON array of the select options' names (schema metadata)
    formula_definition TEXT,               -- the formula, or the lookup or rollup definition in words
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (run_id, airtable_field_id)
);
CREATE INDEX IF NOT EXISTS idx_emergency_results_table ON emergency_field_results(base_id, table_id, run_at);
