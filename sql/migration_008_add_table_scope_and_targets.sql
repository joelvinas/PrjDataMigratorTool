-- Migration 008: table scope and target decisions (from the Migration Design prompt). Adds two tables: table_scope and table_targets.
-- Additive only: no existing table or column is changed. Safe to run more than once.
-- PROPOSED, NOT YET APPLIED. Review it, then apply it yourself (or tell me to), the same way as migration 007.
-- It is deliberately NOT yet in DatabaseSchemaLibrary.sql, the embedded DDL in server.py or sql/apply_migrations.py: the app refuses to
-- connect to a database that lacks an expected table, so those change together, after this is applied.
-- The name review page already reads table_scope when it exists, and shows every table as 'undecided' until then.

-- Which tables are relevant is a human decision, not part of the ingested table definition, so it lives here and a schema re-ingest can
-- never overwrite it. A table with NO row is UNDECIDED, which is different from 'out'. The name validator and everything downstream run on
-- every table that is not explicitly 'out'.
CREATE TABLE IF NOT EXISTS table_scope (
    base_id TEXT NOT NULL,
    table_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('in', 'out')),
    decided_by TEXT NOT NULL,         -- initials or a short name (the app has no logins)
    decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason TEXT,
    PRIMARY KEY (base_id, table_id),
    FOREIGN KEY (table_id) REFERENCES airtable_tables(table_id)
);
CREATE INDEX IF NOT EXISTS idx_table_scope_decision ON table_scope(base_id, decision);

-- For each in-scope table: does it land in Workfront Planning only, or in Planning plus a Workfront Core presence (attachments, a front-facing
-- data entry form, or a defined major workflow)? A table with no row has no target decision yet.
CREATE TABLE IF NOT EXISTS table_targets (
    table_id TEXT NOT NULL PRIMARY KEY,
    base_id TEXT NOT NULL,
    target TEXT NOT NULL CHECK(target IN ('planning_only', 'planning_and_core')),
    decided_by TEXT NOT NULL,
    decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason TEXT,
    FOREIGN KEY (table_id) REFERENCES airtable_tables(table_id)
);
CREATE INDEX IF NOT EXISTS idx_table_targets_base ON table_targets(base_id);
