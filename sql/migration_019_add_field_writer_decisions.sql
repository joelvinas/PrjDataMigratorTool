-- Migration 019 (applied 14 November 2026): the decisions a person records about field writers. Adds TWO tables: field_writer_decisions and field_writer_events.
-- Additive only. Safe to run more than once. Holds field IDs, a choice, who, when and why. No record value.
--
-- Why: the field-writer matrix (who fills each field) is COMPUTED each time from the recorded automations and forms, so it is not stored. What a
-- person decides about it is stored here (approved in advance by the owner, 14 November 2026):
--   formula_only fields (a formula, lookup, rollup or count set by no automation):   recompute | park
--   automation_only fields (set by an automation and by no form):                     fusion_owned | go_stale | users_also_edit
-- 'users_also_edit' means people also type into the field, so it is not automation-only: the Fusion-driven marker still applies.
-- The tool proposes the category; a person decides; nothing is decided silently.
CREATE TABLE IF NOT EXISTS field_writer_decisions (
    airtable_field_id TEXT NOT NULL PRIMARY KEY,
    base_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('recompute', 'park', 'fusion_owned', 'go_stale', 'users_also_edit')),
    decided_by TEXT NOT NULL,                    -- initials or a short name (the app has no logins)
    decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason TEXT,
    FOREIGN KEY (airtable_field_id) REFERENCES airtable_fields(field_id)
);
CREATE INDEX IF NOT EXISTS idx_field_writer_decisions_base ON field_writer_decisions(base_id);

-- Append-only history, so a changed or cleared decision does not lose what it was (the lesson of migration 018).
CREATE TABLE IF NOT EXISTS field_writer_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    airtable_field_id TEXT NOT NULL,
    base_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('decide', 'change', 'clear')),
    old_decision TEXT,                           -- NULL when there was none before
    new_decision TEXT,                           -- NULL after a clear
    actor TEXT NOT NULL,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (airtable_field_id) REFERENCES airtable_fields(field_id)
);
CREATE INDEX IF NOT EXISTS idx_field_writer_events_field ON field_writer_events(airtable_field_id, event_id);
