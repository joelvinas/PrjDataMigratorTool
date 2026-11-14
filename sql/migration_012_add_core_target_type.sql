-- Migration 012: a Workfront data type per system. Adds ONE column to ONE table: field_names.core_target_type.
-- Additive only: nothing is renamed or dropped, existing rows keep their values (the new column is NULL for them).
-- PROPOSED, NOT YET APPLIED (authorised in principle by the owner on 2026-11-14; review the DDL, then confirm it is applied).
-- SQLite has no ADD COLUMN IF NOT EXISTS, so this statement is NOT repeatable on its own: run it once. When it is folded into the schema
-- library, sql/apply_migrations.py will check PRAGMA table_info first, the way it does for migrations 001 and 002.
-- It is deliberately NOT yet in DatabaseSchemaLibrary.sql or the DDL embedded in server.py (a test requires them to be equal, and the
-- fold-in happens after the owner confirms it is applied).

-- field_names.target_type keeps meaning "the Workfront Planning data type" (it is what the spreadsheet's new_dataType fills today).
-- core_target_type is the Workfront Core data type of the same field. NULL = not decided, which the loader reports as blocked for Core.
-- Planning and Core have different type sets, so a field may need two answers.
ALTER TABLE field_names ADD COLUMN core_target_type TEXT;
