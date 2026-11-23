-- Migration 028 (applied by the owner and folded into the schema library, the embedded DDL and apply_migrations.py on 23 November 2026): the list of valid custom forms of the calculated fields page.
-- Adds ONE table. Additive only, safe to run more than once. Touches no existing table.
-- Holds metadata only: a Core custom form (CTGY) ID and its name, the order of the upload, when it was uploaded and the file name. No record value, no credential.
--
-- Why: the calculated fields page reads or changes a custom form only when its ID is on an uploaded list (CSV: category_id, name). Until now the list lived in memory and had to be
-- uploaded again after every restart. This table keeps the last accepted upload. A new accepted upload replaces every row (all or nothing); a refused file changes nothing.
-- The list is one list for the whole tool, as it was in memory (it is not split by environment).
--
-- A new database has it from the library; apply_migrations.py runs this file through src/draftmigrations.py for an older database.

CREATE TABLE IF NOT EXISTS calc_valid_forms (
    category_id TEXT PRIMARY KEY,          -- the Core CTGY ID
    name TEXT NOT NULL,                    -- the form's name as the uploaded list gives it
    position INTEGER NOT NULL,             -- the row order of the upload, so the list comes back in the same order
    uploaded_at TEXT NOT NULL,             -- UTC
    source_filename TEXT                   -- the name of the uploaded file, for display only
);
