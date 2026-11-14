-- Migration 018 (PROPOSED, not applied): the history of link direction decisions. Adds ONE table: link_direction_events.
-- Additive only. Safe to run more than once. Holds field IDs, direction choices, who, when and why. No record value.
--
-- Why: field_relationships (migration 015) keeps only the LATEST decision per link field, so a changed or cleared direction loses what it was
-- before. This table is append-only: every confirm, change, clear and auto-set is one row, so an audit can show what was decided, by whom, when
-- and why, in order. The app writes to it only when the table exists, so nothing breaks before this is applied.
CREATE TABLE IF NOT EXISTS link_direction_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    airtable_field_id TEXT NOT NULL,              -- the link field the event is about
    base_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('confirm', 'change', 'clear', 'auto_set')),   -- auto_set = the partner of a field a person chose on
    old_relationship_type TEXT,                   -- NULL when there was no decision before
    old_tiebreaker TEXT,
    new_relationship_type TEXT,                   -- NULL after a clear
    new_tiebreaker TEXT,
    actor TEXT NOT NULL,                          -- initials or a short name (the app has no logins)
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (airtable_field_id) REFERENCES airtable_fields(field_id)
);
CREATE INDEX IF NOT EXISTS idx_link_direction_events_field ON link_direction_events(airtable_field_id, event_id);
