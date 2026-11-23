-- Migration 028 (DRAFT, approved by the owner on 22 November 2026; not yet applied): the list of valid custom forms of the calculated fields page.
-- Adds ONE table. Additive only, safe to run more than once. Touches no existing table.
-- Holds metadata only: a Core custom form (CTGY) ID and its name, the order of the upload, when it was uploaded and the file name. No record value, no credential.
--
-- Why: the calculated fields page reads or changes a custom form only when its ID is on an uploaded list (CSV: category_id, name). Until now the list lived in memory and had to be
-- uploaded again after every restart. This table keeps the last accepted upload. A new accepted upload replaces every row (all or nothing); a refused file changes nothing.
-- The list is one list for the whole tool, as it was in memory (it is not split by environment).
--
-- Not yet in DatabaseSchemaLibrary.sql, the embedded DDL in server.py or sql/apply_migrations.py: that waits until the owner confirms it is applied.
-- Until then src/draftmigrations.py applies it and the page falls back to keeping the list in memory.

CREATE TABLE IF NOT EXISTS calc_valid_forms (
    category_id TEXT PRIMARY KEY,          -- the Core CTGY ID
    name TEXT NOT NULL,                    -- the form's name as the uploaded list gives it
    position INTEGER NOT NULL,             -- the row order of the upload, so the list comes back in the same order
    uploaded_at TEXT NOT NULL,             -- UTC
    source_filename TEXT                   -- the name of the uploaded file, for display only
);
