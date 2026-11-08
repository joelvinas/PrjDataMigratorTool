-- Migration 002: add the field-settings columns to airtable_fields in a database created before this version.
-- A database created by this version of the app already has them; do not run this on it.
-- Easiest way: python sql/apply_migrations.py path/to/your.db   (skips anything already present, safe to repeat)
-- Or run the statements below once with any SQLite client. Existing rows stay NULL until the schema is ingested again.
ALTER TABLE airtable_fields ADD COLUMN field_description TEXT;
ALTER TABLE airtable_fields ADD COLUMN is_primary_field INTEGER;
ALTER TABLE airtable_fields ADD COLUMN options_json TEXT;
ALTER TABLE airtable_fields ADD COLUMN choices_json TEXT;
ALTER TABLE airtable_fields ADD COLUMN formula_text TEXT;
ALTER TABLE airtable_fields ADD COLUMN referenced_field_ids TEXT;
ALTER TABLE airtable_fields ADD COLUMN source_link_field_id TEXT;
ALTER TABLE airtable_fields ADD COLUMN source_field_id TEXT;
ALTER TABLE airtable_fields ADD COLUMN is_valid INTEGER;
ALTER TABLE airtable_fields ADD COLUMN result_type TEXT;
ALTER TABLE airtable_fields ADD COLUMN result_options_json TEXT;
ALTER TABLE airtable_fields ADD COLUMN number_precision INTEGER;
ALTER TABLE airtable_fields ADD COLUMN currency_symbol TEXT;
ALTER TABLE airtable_fields ADD COLUMN date_format TEXT;
ALTER TABLE airtable_fields ADD COLUMN time_format TEXT;
ALTER TABLE airtable_fields ADD COLUMN time_zone TEXT;
ALTER TABLE airtable_fields ADD COLUMN duration_format TEXT;
ALTER TABLE airtable_fields ADD COLUMN rating_max INTEGER;
ALTER TABLE airtable_fields ADD COLUMN icon TEXT;
ALTER TABLE airtable_fields ADD COLUMN option_color TEXT;
