-- Migration 012: a Workfront data type per system. Adds ONE column to ONE table: field_names.core_target_type.
-- Additive only: nothing is renamed or dropped, existing rows keep their values (the new column is NULL for them).
-- Applied to the live database on 2026-11-14 (backed up first) and folded into sql/DatabaseSchemaLibrary.sql, the DDL embedded in server.py and
-- sql/apply_migrations.py. Apply it to an existing database with: python sql/apply_migrations.py path/to/your.db (back it up first).
-- ADD COLUMN cannot be repeated: apply_migrations.py checks PRAGMA table_info first.

-- field_names.target_type keeps meaning "the Workfront Planning data type" (it is what the spreadsheet's new_dataType fills today).
-- core_target_type is the Workfront Core data type of the same field. NULL = not decided, which the loader reports as blocked for Core.
-- Planning and Core have different type sets, so a field may need two answers.
ALTER TABLE field_names ADD COLUMN core_target_type TEXT;
