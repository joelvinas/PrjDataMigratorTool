-- Migration 003: add the table-level and view tables. Uses IF NOT EXISTS, so it is safe to repeat.
-- A database created by this version of the app already has them.
-- Easiest way: python sql/apply_migrations.py path/to/your.db
-- Both tables stay empty until each base's schema is ingested again.
CREATE TABLE IF NOT EXISTS airtable_tables (
    table_id TEXT NOT NULL PRIMARY KEY,
    base_id TEXT NOT NULL,
    table_name TEXT NOT NULL,
    table_description TEXT,
    primary_field_id TEXT,
    table_json TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_airtable_tables_base ON airtable_tables(base_id);
CREATE TABLE IF NOT EXISTS airtable_views (
    view_id TEXT NOT NULL PRIMARY KEY,
    base_id TEXT NOT NULL,
    table_id TEXT NOT NULL,
    view_name TEXT NOT NULL,
    view_type TEXT NOT NULL,
    view_position INTEGER,
    visible_field_ids TEXT,
    view_json TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (table_id) REFERENCES airtable_tables(table_id)
);
CREATE INDEX IF NOT EXISTS idx_airtable_views_table ON airtable_views(table_id);
CREATE INDEX IF NOT EXISTS idx_airtable_views_type ON airtable_views(view_type);
