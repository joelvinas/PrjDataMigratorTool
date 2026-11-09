-- Migration 004: attachment size and type statistics. Adds one table; changes nothing that exists.
-- Safe to run more than once. (python sql/apply_migrations.py applies this for you.)

CREATE TABLE IF NOT EXISTS airtable_attachment_stats (
    base_id TEXT NOT NULL,
    table_id TEXT NOT NULL,
    field_id TEXT NOT NULL,
    file_extension TEXT NOT NULL,   -- from the attachment filename, else the MIME type, else 'bin'
    mime_type TEXT NOT NULL,        -- as reported by Airtable, lower case, 'unknown' if none
    file_count INTEGER NOT NULL DEFAULT 0,
    total_bytes INTEGER NOT NULL DEFAULT 0,
    min_bytes INTEGER,
    max_bytes INTEGER,
    unknown_size_count INTEGER NOT NULL DEFAULT 0,  -- attachments for which Airtable reported no size
    size_lt_1mb INTEGER NOT NULL DEFAULT 0,         -- size buckets use 1 MB = 1,048,576 bytes
    size_1_10mb INTEGER NOT NULL DEFAULT 0,
    size_10_100mb INTEGER NOT NULL DEFAULT 0,
    size_100mb_1gb INTEGER NOT NULL DEFAULT 0,
    size_ge_1gb INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (field_id, file_extension, mime_type)
);

CREATE INDEX IF NOT EXISTS idx_attachment_stats_table ON airtable_attachment_stats(table_id);
CREATE INDEX IF NOT EXISTS idx_attachment_stats_base ON airtable_attachment_stats(base_id);

