-- Migration 008: table scope and target decisions (from the Migration Design prompt). Adds two tables: table_scope and table_targets.
-- Additive only: no existing table or column is changed. Safe to run more than once.
-- Approved by the owner on 2026-11-14 and folded into sql/DatabaseSchemaLibrary.sql, the DDL embedded in server.py and
-- sql/apply_migrations.py. Apply it to an existing database with: python sql/apply_migrations.py path/to/your.db (back it up first).
-- Safe to run again: nothing happens when the tables already exist.

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
