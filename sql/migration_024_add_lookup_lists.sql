-- Migration 024 (DRAFT, not applied): lookup lists. Adds THREE tables. Additive only, safe to run more than once. Touches no existing table.
-- Holds field IDs, list names, who, when and why. No record value and no option text.
--
-- Why: a select field with a very long option list (hundreds of options, such as a list of cities) is not realistic as a select field. A person can
-- authorize it to be migrated as a LOOKUP LIST instead: a Workfront Planning record type of its own, with one record per option, loaded before the table that
-- uses it; the select field then becomes a one-sided Planning reference to it. See docs/LOADER_PLAN.md, "Long option lists as their own record type".
-- This migration stores only the DECISIONS (which fields, which list, who, when, why). The loader's own register (the record type's Planning ID and each
-- option's record ID) is a later, separate proposal.

-- A lookup list: the name of the record type to be created in Planning. The name is a Planning name and follows the Planning naming rules (checked by the app).
CREATE TABLE IF NOT EXISTS lookup_lists (
    list_id INTEGER PRIMARY KEY AUTOINCREMENT,
    base_id TEXT NOT NULL,
    list_name TEXT NOT NULL,
    created_by TEXT NOT NULL,                    -- initials or a short name (the app has no logins)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lookup_lists_name ON lookup_lists(base_id, lower(list_name));

-- The latest decision per select field: this field's options go to this list. A field with no row stays an ordinary select field.
-- Several fields may share one list.
CREATE TABLE IF NOT EXISTS field_lookup_decisions (
    airtable_field_id TEXT NOT NULL PRIMARY KEY,
    list_id INTEGER NOT NULL,
    decided_by TEXT NOT NULL,
    decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason TEXT,
    FOREIGN KEY (airtable_field_id) REFERENCES airtable_fields(field_id),
    FOREIGN KEY (list_id) REFERENCES lookup_lists(list_id)
);

-- Append-only history (the lesson of migrations 018 and 019): rows are never updated or deleted. A bulk change shares one batch_id.
CREATE TABLE IF NOT EXISTS lookup_list_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT,
    base_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('list_create', 'list_rename', 'list_delete', 'assign', 'unassign')),
    list_id INTEGER,
    airtable_field_id TEXT,
    old_list_id INTEGER,
    old_name TEXT,
    new_name TEXT,
    actor TEXT NOT NULL,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_lookup_list_events_field ON lookup_list_events(airtable_field_id, event_id);
CREATE INDEX IF NOT EXISTS idx_lookup_list_events_batch ON lookup_list_events(batch_id);
