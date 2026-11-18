-- Migration 022 (DRAFT, not applied): the Core displayType, and a clearly named view. Adds ONE column to ONE table and ONE view. Additive only.
-- Run migration 021 first. ADD COLUMN cannot be repeated: sql/apply_migrations.py checks PRAGMA table_info first (once this migration is folded in).
-- The Core API needs a dataType and a displayType, so they are two columns: core_target_type (migration 012) holds the dataType and this holds the displayType.
-- Existing rows keep NULL here. NULL = not decided, which the loader reports as blocked for Core.
ALTER TABLE field_names ADD COLUMN core_display_type TEXT;

-- The clear names, without renaming a column: target_type is the Planning data type.
CREATE VIEW IF NOT EXISTS field_data_types AS
    SELECT airtable_field_id, target_type AS planning_target_type, core_target_type, core_display_type FROM field_names;
