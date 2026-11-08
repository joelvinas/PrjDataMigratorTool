-- Migration 001: add two columns to airtable_fields in a database created from the ORIGINAL schema.
-- A database created by this version of the app already has them; do not run this on it.
-- Run once, with any SQLite client (for example the sqlite3 command line, or Python's sqlite3 module).

-- 1 = the link field accepts a single linked record (options.prefersSingleRecordLink); 0 = many; NULL = not a link
-- field, or Airtable did not report it. Existing rows stay NULL until the schema is ingested again.
ALTER TABLE airtable_fields ADD COLUMN prefers_single_record_link INTEGER;

-- 1 = cardinality is a lower bound (the field has more distinct values than the cap); 0 = exact.
ALTER TABLE airtable_fields ADD COLUMN cardinality_is_capped INTEGER DEFAULT 0;
