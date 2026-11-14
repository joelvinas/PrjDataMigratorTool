-- Migration 007: field and table name review (the name validator). Adds three tables: field_names, table_names, name_events.
-- Additive only: no existing table or column is changed. Safe to run more than once.
-- Applied to the live database on 2026-11-13 (backed up first) and folded into sql/DatabaseSchemaLibrary.sql, the DDL embedded in
-- server.py and sql/apply_migrations.py. Safe to run again: nothing happens when the tables already exist.

-- One row per Airtable field (keyed by the stable Airtable field ID): the names a person chose or imported, the approval state of each
-- target, the description and Workfront type, and the abandon decision. Names are validated per target (see fieldnames.py).
CREATE TABLE IF NOT EXISTS field_names (
    airtable_field_id TEXT NOT NULL PRIMARY KEY,
    planning_name TEXT,               -- Workfront Planning field name (NULL until imported or approved)
    core_name TEXT,                   -- Workfront Core custom field name
    core_label TEXT,                  -- Workfront Core custom field label
    planning_name_status TEXT NOT NULL DEFAULT 'unreviewed' CHECK(planning_name_status IN ('unreviewed', 'approved', 'needs_human')),
    core_name_status TEXT NOT NULL DEFAULT 'unreviewed' CHECK(core_name_status IN ('unreviewed', 'approved', 'needs_human')),
    core_label_status TEXT NOT NULL DEFAULT 'unreviewed' CHECK(core_label_status IN ('unreviewed', 'approved', 'needs_human')),
    description TEXT,                 -- generated from the lineage syntax, then hand-edited
    target_type TEXT,                 -- target Workfront data type
    planning_name_verdict TEXT,       -- cache of the last validation: PASS, AUTO_FIXABLE or NEEDS_HUMAN (always recomputable)
    core_name_verdict TEXT,
    core_label_verdict TEXT,
    verdict_rules TEXT,               -- JSON: rule IDs per target from the last validation
    validated_at TIMESTAMP,
    abandoned INTEGER NOT NULL DEFAULT 0,   -- 1 = a person abandoned this field (never automatic)
    abandoned_by TEXT,
    abandoned_at TIMESTAMP,
    abandoned_reason TEXT,
    import_batch TEXT,                -- which spreadsheet import last set the names
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (airtable_field_id) REFERENCES airtable_fields(field_id)
);
CREATE INDEX IF NOT EXISTS idx_field_names_abandoned ON field_names(abandoned);

-- One row per Airtable table: the new table name a person approved. The suggestion itself is always recomputed, never stored.
-- The table-name swap in field names reads this (approved name first, else the suggestion).
CREATE TABLE IF NOT EXISTS table_names (
    airtable_table_id TEXT NOT NULL PRIMARY KEY,
    new_table_name TEXT,
    status TEXT NOT NULL DEFAULT 'unreviewed' CHECK(status IN ('unreviewed', 'approved', 'needs_human')),
    approved_by TEXT,
    approved_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (airtable_table_id) REFERENCES airtable_tables(table_id)
);

-- Append-only audit trail of every human decision and of the settings in force (toggles and presets), so any run can be reproduced.
-- Rows are never updated or deleted. Current settings for a base = its newest 'settings' event. No record values are ever stored here.
CREATE TABLE IF NOT EXISTS name_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_type TEXT NOT NULL CHECK(subject_type IN ('field', 'table', 'settings')),
    subject_id TEXT NOT NULL,         -- Airtable field ID, Airtable table ID, or the base ID for settings
    event_type TEXT NOT NULL CHECK(event_type IN ('import', 'approve', 'reject', 'needs_human', 'abandon', 'unabandon', 'settings')),
    target TEXT,                      -- planning_name, core_name, core_label (NULL when it applies to the whole field or table)
    old_value TEXT,
    new_value TEXT,                   -- for 'settings': the JSON of the toggles and presets
    actor TEXT NOT NULL,              -- who decided (free text: the app has no logins)
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_name_events_subject ON name_events(subject_type, subject_id);
