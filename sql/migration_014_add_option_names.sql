-- Migration 014: select option names and their Workfront option IDs. Adds TWO tables: option_names and option_workfront_ids.
-- Additive only: no existing table or column is changed. Safe to run more than once.
-- Applied to the live database on 2026-11-14 (backed up first) and folded into sql/DatabaseSchemaLibrary.sql, the DDL embedded in server.py and
-- sql/apply_migrations.py. Apply it to an existing database with: python sql/apply_migrations.py path/to/your.db (back it up first).

-- Option names follow the Core label rules. The tool proposes a repair for a name that breaks them; a person approves it here, with who, when
-- and why. The Airtable option is identified by its stable choice ID (airtable_fields.choices_json), never by its name. An option whose name
-- breaks the rules and has no approved row stays on hold in the load plan. No record values are stored.
CREATE TABLE IF NOT EXISTS option_names (
    airtable_field_id TEXT NOT NULL,
    choice_id TEXT NOT NULL,          -- the Airtable select option's ID
    approved_name TEXT NOT NULL,      -- the name to create in Workfront
    approved_by TEXT NOT NULL,        -- initials or a short name (the app has no logins)
    approved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason TEXT,
    PRIMARY KEY (airtable_field_id, choice_id),
    FOREIGN KEY (airtable_field_id) REFERENCES airtable_fields(field_id)
);

-- The option IDs PULLED BACK from Workfront, so what exists there can be compared with what was approved. Core: the option object (POPT, a
-- parameter option, per Workfront's API as reported by the community; not confirmed in Adobe's pages) of a custom field. Planning: the select
-- option ID of a Planning field, in a Workspace. IDs differ per stage, so a row is per Core environment or per Planning Workspace.
-- load_id_map (migration 010) records what the loader CREATED; this records what was READ BACK, with the name Workfront reports.
CREATE TABLE IF NOT EXISTS option_workfront_ids (
    airtable_field_id TEXT NOT NULL,
    choice_id TEXT NOT NULL,
    system TEXT NOT NULL CHECK(system IN ('core', 'planning')),
    environment TEXT NOT NULL CHECK(environment IN ('sandbox1', 'sandbox2', 'preview', 'production', 'dev', 'uat')),
    workspace_id TEXT NOT NULL DEFAULT '',   -- the Planning Workspace (empty for Core)
    workfront_option_id TEXT NOT NULL,       -- the Core POPT ID, or the Planning option ID
    workfront_name TEXT,                     -- the name Workfront reports for it
    position INTEGER,                        -- its position in Workfront's order
    pulled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (airtable_field_id, choice_id, system, environment, workspace_id),
    FOREIGN KEY (airtable_field_id) REFERENCES airtable_fields(field_id)
);
CREATE INDEX IF NOT EXISTS idx_option_workfront_ids_wf ON option_workfront_ids(system, workfront_option_id);
