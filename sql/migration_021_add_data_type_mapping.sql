-- Migration 021 (DRAFT, not applied): the data type mapping per system. Adds THREE tables. Additive only, safe to run more than once.
-- Holds field IDs, type names, who, when and why. No record value. Run migration 022 (the column and the view) after this one.
--
-- Why: Workfront Planning and Workfront Core do not share one data type list, so a field needs two answers.
--   field_names.target_type        = the Planning data type (kept; nothing is renamed). Migration 022 shows it as planning_target_type in a view.
--   field_names.core_target_type   = the Core dataType (migration 012), for example TEXT, NMBR, CURC, DATE, DTTM, RICH.
--   field_names.core_display_type  = the Core displayType (migration 022), for example TEXT, TXTA, SLCT, MULT, CHCK. The Core API needs both, so they are two fields.
-- The tool proposes a type from a default mapping per Airtable type; a person applies it, overrides it, or sets many fields at once.
-- The chosen value is written to the field_names columns (what the loader reads); the tables below record who decided, when and why.
-- For Planning, the display type columns stay NULL.

-- The default mapping: one row per (system, Airtable field type). A person may edit a default; every edit goes to data_type_events.
-- NULL default_type = the tool proposes nothing and a person must decide.
CREATE TABLE IF NOT EXISTS data_type_defaults (
    system TEXT NOT NULL CHECK(system IN ('planning', 'core')),
    airtable_type TEXT NOT NULL,
    default_type TEXT,                           -- Planning type, or the Core dataType
    default_display_type TEXT,                   -- Core displayType; NULL for Planning
    note TEXT,
    updated_by TEXT NOT NULL DEFAULT 'seed',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (system, airtable_type)
);

-- Who set each field's type for each system, and how: default (taken from the mapping), override (a person chose another), import (spreadsheet).
CREATE TABLE IF NOT EXISTS field_type_decisions (
    airtable_field_id TEXT NOT NULL,
    system TEXT NOT NULL CHECK(system IN ('planning', 'core')),
    chosen_type TEXT NOT NULL,
    chosen_display_type TEXT,                    -- Core only
    source TEXT NOT NULL CHECK(source IN ('default', 'override', 'import')),
    decided_by TEXT NOT NULL,                    -- initials or a short name (the app has no logins)
    decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason TEXT,
    PRIMARY KEY (airtable_field_id, system),
    FOREIGN KEY (airtable_field_id) REFERENCES airtable_fields(field_id)
);

-- Append-only history (the lesson of migrations 018 and 019). A bulk update shares one batch_id. Default edits have airtable_field_id = NULL.
-- 'clear' also records a value that was not on the allowed list and was removed (the old value is kept here, so nothing is lost).
CREATE TABLE IF NOT EXISTS data_type_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT,
    airtable_field_id TEXT,
    system TEXT NOT NULL CHECK(system IN ('planning', 'core')),
    airtable_type TEXT,                          -- set on default edits
    event_type TEXT NOT NULL CHECK(event_type IN ('set', 'change', 'clear', 'default_change')),
    old_type TEXT,
    old_display_type TEXT,
    new_type TEXT,
    new_display_type TEXT,
    source TEXT,
    actor TEXT NOT NULL,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_data_type_events_field ON data_type_events(airtable_field_id, event_id);
CREATE INDEX IF NOT EXISTS idx_data_type_events_batch ON data_type_events(batch_id);

-- Default mapping, Planning (the 17 types in the Adobe API documentation). Anything not listed has no default.
INSERT OR IGNORE INTO data_type_defaults(system, airtable_type, default_type, default_display_type, note) VALUES
    ('planning', 'singleLineText', 'text', NULL, NULL), ('planning', 'email', 'text', NULL, NULL), ('planning', 'url', 'text', NULL, NULL), ('planning', 'phoneNumber', 'text', NULL, NULL),
    ('planning', 'multilineText', 'long-text', NULL, NULL), ('planning', 'richText', 'long-text', NULL, 'Planning has no rich text'),
    ('planning', 'number', 'number', NULL, NULL), ('planning', 'rating', 'number', NULL, NULL), ('planning', 'duration', 'number', NULL, 'Seconds; review'),
    ('planning', 'percent', 'percentage', NULL, NULL), ('planning', 'currency', 'currency', NULL, NULL),
    ('planning', 'date', 'date', NULL, NULL), ('planning', 'dateTime', 'date', NULL, 'Planning date has no time of day; review'),
    ('planning', 'checkbox', 'boolean', NULL, NULL),
    ('planning', 'singleSelect', 'single-select', NULL, NULL), ('planning', 'multipleSelects', 'multi-select', NULL, NULL),
    ('planning', 'singleCollaborator', 'user', NULL, NULL), ('planning', 'multipleCollaborators', 'user', NULL, NULL),
    ('planning', 'multipleRecordLinks', 'reference', NULL, NULL),
    ('planning', 'createdTime', 'created-at', NULL, NULL), ('planning', 'lastModifiedTime', 'updated-at', NULL, NULL),
    ('planning', 'createdBy', 'created-by', NULL, NULL), ('planning', 'lastModifiedBy', 'updated-by', NULL, NULL),
    ('planning', 'formula', 'formula', NULL, 'Derived: stays on hold in the load plan'),
    ('planning', 'autoNumber', NULL, NULL, 'record-id is not an Airtable autonumber; a person decides'),
    ('planning', 'multipleAttachments', NULL, NULL, 'No Planning type; handled by the attachment pass');

-- Default mapping, Core: dataType and displayType as separate values, as in src/typemap.py (SLCT, MULT and CHCK are not yet verified against a sandbox).
INSERT OR IGNORE INTO data_type_defaults(system, airtable_type, default_type, default_display_type, note) VALUES
    ('core', 'singleLineText', 'TEXT', 'TEXT', NULL), ('core', 'email', 'TEXT', 'TEXT', NULL), ('core', 'url', 'TEXT', 'TEXT', NULL), ('core', 'phoneNumber', 'TEXT', 'TEXT', NULL),
    ('core', 'multilineText', 'TEXT', 'TXTA', NULL), ('core', 'richText', NULL, NULL, 'RICH cannot be created through the API; built by hand'),
    ('core', 'number', 'NMBR', 'TEXT', NULL), ('core', 'rating', 'NMBR', 'TEXT', NULL), ('core', 'duration', 'NMBR', 'TEXT', 'Seconds; review'),
    ('core', 'percent', 'NMBR', 'TEXT', NULL), ('core', 'currency', 'CURC', 'TEXT', NULL),
    ('core', 'date', 'DATE', 'TEXT', NULL), ('core', 'dateTime', 'DTTM', 'TEXT', NULL),
    ('core', 'checkbox', 'TEXT', 'CHCK', 'Unverified'),
    ('core', 'singleSelect', 'TEXT', 'SLCT', 'Unverified'), ('core', 'multipleSelects', 'TEXT', 'MULT', 'Unverified'),
    ('core', 'singleCollaborator', NULL, NULL, 'No Core custom field type in this loader'), ('core', 'multipleCollaborators', NULL, NULL, 'No Core custom field type in this loader'),
    ('core', 'multipleRecordLinks', NULL, NULL, 'Core has no link field'),
    ('core', 'createdTime', NULL, NULL, 'Derived'), ('core', 'lastModifiedTime', NULL, NULL, 'Derived'), ('core', 'createdBy', NULL, NULL, 'Derived'), ('core', 'lastModifiedBy', NULL, NULL, 'Derived'),
    ('core', 'formula', NULL, NULL, 'Derived'), ('core', 'autoNumber', NULL, NULL, 'Derived'), ('core', 'multipleAttachments', NULL, NULL, 'Handled by the attachment pass');
