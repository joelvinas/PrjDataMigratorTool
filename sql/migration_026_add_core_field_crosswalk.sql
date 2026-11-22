-- Migration 026 (DRAFT, not applied): the crosswalk of the Core bulk field creator. Adds ONE table. Additive only, safe to run more than once. Touches no existing table.
-- Holds field metadata and Workfront IDs only (names, labels, types, descriptions, the new parameterId). No record value and no credential.
--
-- Why: the existing field_crosswalk keeps one dev ID and one prod ID per Airtable field. The Core bulk field creator (page /corefields) needs several Core fields per
-- Airtable field ID, one set per Core environment (sandbox1, sandbox2, preview, production), and the facts of what was sent (name, label, dataType, displayType,
-- description, isRequired) beside the new parameterId and its lastUpdateDate. One row per Core field this tool created. The Reset button deletes the parameter in Workfront
-- and then deletes its row here; it never deletes a parameter that has no row (a field that already existed was not made by this tool).
-- Until the owner applies it, the page still works and the crosswalk lives in memory for the session and in the CSV download, but nothing is kept between runs.
--
-- Not folded into DatabaseSchemaLibrary.sql, the embedded DDL in server.py or sql/apply_migrations.py until the owner confirms it is applied (a test requires the library
-- file to equal the embedded DDL, and the app refuses a database that lacks an expected table).

CREATE TABLE IF NOT EXISTS core_field_crosswalk (
    crosswalk_id INTEGER PRIMARY KEY AUTOINCREMENT,
    environment TEXT NOT NULL CHECK(environment IN ('sandbox1', 'sandbox2', 'preview', 'production')),
    airtable_field_id TEXT NOT NULL,       -- the field_id column of the upload (an Airtable field ID); several rows may share it
    workfront_name TEXT NOT NULL,
    workfront_label TEXT,
    workfront_data_type TEXT NOT NULL,     -- CURC, DATE, NMBR or TEXT
    workfront_display_type TEXT NOT NULL,  -- CHCK or TEXT
    workfront_description TEXT,
    workfront_is_required INTEGER NOT NULL DEFAULT 0 CHECK(workfront_is_required IN (0, 1)),
    parameter_id TEXT NOT NULL,            -- the Core PARAM ID Workfront returned
    last_update_date TEXT,                 -- the parameter's lastUpdateDate as Workfront reported it
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (environment, parameter_id),
    UNIQUE (environment, airtable_field_id, workfront_name)
);
CREATE INDEX IF NOT EXISTS idx_core_field_crosswalk_env ON core_field_crosswalk(environment, airtable_field_id);
