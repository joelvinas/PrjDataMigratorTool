-- Migration 017 (PROPOSED, not applied): the record ID map. Adds FOUR tables: source_records, target_mappings, record_pass_markers, attachment_map.
-- Additive only. Safe to run more than once. Holds IDs, statuses, file sizes and hashes ONLY: no record value, no file name, no link.
--
-- Design (owner, 14 November 2026): every Airtable record gets ONE surrogate ID, and everything else points to it instead of repeating base,
-- table and record ID on every row. The surrogate is an integer assigned when the record is first registered; records are registered in sorted
-- order (base, table, record ID) so the same input gives the same numbers (the app has no randomness at runtime, so no random UUID).

-- 1. The registry: one row per Airtable record, the three IDs stored once.
CREATE TABLE IF NOT EXISTS source_records (
    source_record_uid INTEGER PRIMARY KEY AUTOINCREMENT,
    base_id TEXT NOT NULL,
    table_id TEXT NOT NULL,
    record_id TEXT NOT NULL,                  -- the Airtable record ID (recXXXX)
    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (base_id, table_id, record_id),    -- the same record can never be registered twice
    FOREIGN KEY (table_id) REFERENCES airtable_tables(table_id)
);
CREATE INDEX IF NOT EXISTS idx_source_records_table ON source_records(base_id, table_id);

-- 2. Target mappings: one row per target record. Consolidation (several source records into one target) and fan-out (one source into several
-- targets) are just more rows against the same surrogate. workspace_key is the Planning Workspace ID, or '' for Core.
CREATE TABLE IF NOT EXISTS target_mappings (
    mapping_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_record_uid INTEGER NOT NULL,
    system TEXT NOT NULL CHECK(system IN ('planning', 'core')),
    environment TEXT NOT NULL CHECK(environment IN ('sandbox1', 'sandbox2', 'preview', 'production', 'dev', 'uat')),
    workspace_key TEXT NOT NULL DEFAULT '',
    target_kind TEXT NOT NULL,                -- for example 'record' (Planning) or 'project', 'request', 'task' (Core)
    target_record_id TEXT NOT NULL,           -- the new Workfront ID
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_record_uid, system, environment, workspace_key, target_record_id),
    FOREIGN KEY (source_record_uid) REFERENCES source_records(source_record_uid)
);
CREATE INDEX IF NOT EXISTS idx_target_mappings_target ON target_mappings(system, environment, target_record_id);

-- 3. Pass-completed markers: a restart reads these to know what is done and never repeats it. One row per record, pass and stage.
CREATE TABLE IF NOT EXISTS record_pass_markers (
    source_record_uid INTEGER NOT NULL,
    pass TEXT NOT NULL CHECK(pass IN ('records', 'links', 'attachments', 'core_backfill')),
    environment TEXT NOT NULL CHECK(environment IN ('sandbox1', 'sandbox2', 'preview', 'production', 'dev', 'uat')),
    workspace_key TEXT NOT NULL DEFAULT '',
    completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_record_uid, pass, environment, workspace_key),
    FOREIGN KEY (source_record_uid) REFERENCES source_records(source_record_uid)
);

-- 4. Attachment rows: each Airtable attachment ID paired with the Core document ID, so a rerun does not upload a file twice. Airtable returns no
-- checksum, so the hash is of the file the tool downloaded (it proves the transfer and upload, not a match with the source); the API-reported size
-- is the second check. No file name and no link is stored (links expire and are not secret-free).
CREATE TABLE IF NOT EXISTS attachment_map (
    attachment_row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_record_uid INTEGER NOT NULL,
    airtable_field_id TEXT NOT NULL,
    airtable_attachment_id TEXT NOT NULL,     -- attXXXX
    environment TEXT NOT NULL CHECK(environment IN ('sandbox1', 'sandbox2', 'preview', 'production', 'dev', 'uat')),
    core_document_id TEXT,                    -- NULL until the upload returns
    api_size_bytes INTEGER,                   -- the size Airtable reported
    downloaded_bytes INTEGER,                 -- the size of the file as downloaded
    sha256 TEXT,                              -- hash of the downloaded file
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'downloaded', 'uploaded', 'failed')),
    last_error TEXT,                          -- a short message with any credential removed
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_record_uid, airtable_field_id, airtable_attachment_id, environment),
    FOREIGN KEY (source_record_uid) REFERENCES source_records(source_record_uid)
);
