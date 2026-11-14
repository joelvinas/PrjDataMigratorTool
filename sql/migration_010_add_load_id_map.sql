-- Migration 010: the load ID map. Adds ONE table: load_id_map.
-- Additive only: no existing table or column is changed. Safe to run more than once.
-- PROPOSED, NOT YET APPLIED. Review it, then approve it before it is applied.
-- It is deliberately NOT in DatabaseSchemaLibrary.sql, the embedded DDL in server.py or sql/apply_migrations.py: the app refuses to connect to a
-- database that lacks an expected table, so those change together, after this is approved. Nothing in the code needs it yet: the loader's plan
-- and dry run keep their state in memory. It is needed when real creation is added.

-- Every item the loader creates in Workfront (a Planning record type or field, a Core custom field, a select option) has a TEMPORARY ID that
-- depends only on the Airtable IDs (see loadplan.py), such as P-F-fldXXXX. The moment a create call returns, its Workfront ID is written here
-- against the temporary ID, and a restart skips every row that already has one, so nothing is ever created twice. One row per temporary ID
-- per stage: Core has a sandbox and production; Workfront Planning has NO environments, only Workspaces (owner, 14 November 2026): everything goes into
-- one Workspace treated as isolated for Dev, which is then either wiped and recreated as a UAT Workspace or promoted to UAT. So 'environment' is the
-- stage label ('sandbox1', 'sandbox2', 'preview' and 'production' for Core, 'dev' and 'uat' for Planning) and workspace_id names the Planning Workspace the IDs belong to.
-- Promoting Dev to UAT relabels that Workspace's rows (a person's recorded decision); a wipe and recreate makes new rows. Each stage has its own IDs. Option rows (kind 'option') are rows
-- like any other, with their position in Airtable's order, so every select field's options are recorded by ID in order.
-- Every create call's result is ALSO logged against its temporary ID in runbook_logs (failing_component = the temporary ID), the existing log.
-- Nothing here holds a credential or a record value.
CREATE TABLE IF NOT EXISTS load_id_map (
    temp_id TEXT NOT NULL,            -- for example P-T-tblXXXX, P-F-fldXXXX, C-F-fldXXXX, P-O-fldXXXX-selXXXX
    environment TEXT NOT NULL CHECK(environment IN ('sandbox1', 'sandbox2', 'preview', 'production', 'dev', 'uat')),
    workspace_id TEXT,                -- the Planning Workspace these IDs belong to (NULL for Core)
    system TEXT NOT NULL CHECK(system IN ('planning', 'core')),
    kind TEXT NOT NULL CHECK(kind IN ('record_type', 'form', 'field', 'option')),
    base_id TEXT NOT NULL,
    table_id TEXT,                    -- the Airtable table the item belongs to
    airtable_field_id TEXT,           -- the Airtable field (NULL for a record type or form, and for fields the migration adds itself)
    parent_temp_id TEXT,              -- the record type (for a Planning field) or the field (for an option)
    position INTEGER,                 -- an option's position in Airtable's order
    name TEXT NOT NULL,               -- the name sent to Workfront
    workfront_id TEXT,                -- NULL until the create call returns; once set, the item is never created again
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'created', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,                  -- a short message with any credential removed
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (temp_id, environment)
);
CREATE INDEX IF NOT EXISTS idx_load_id_map_base ON load_id_map(base_id, environment);
CREATE INDEX IF NOT EXISTS idx_load_id_map_field ON load_id_map(airtable_field_id);
