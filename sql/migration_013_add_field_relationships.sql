-- Migration 013: stored relationships between tables. Adds ONE table: field_relationships.
-- Additive only: no existing table or column is changed. Safe to run more than once.
-- PROPOSED, NOT YET APPLIED (authorised in principle by the owner on 2026-11-14; review the DDL, then confirm it is applied).
-- It is deliberately NOT in DatabaseSchemaLibrary.sql, the embedded DDL in server.py or sql/apply_migrations.py until the owner confirms it
-- is applied.

-- Relationships are DEFINED BY THE AIRTABLE SCHEMA (airtable_fields.linked_table_id and inverse_link_field_id) and are STORED here so the
-- loader and the reports read one decided answer. The tool proposes: one row per link field, with its paired field when the schema reports
-- one. Exactly one side of a two-way pair is the real Planning relationship (is_primary = 1); the other side is the reverse view. A person
-- approves (approved_by, approved_at); nothing is decided silently, and a row that is not approved keeps the loader's second pass on hold.
-- No record values are stored here.
CREATE TABLE IF NOT EXISTS field_relationships (
    airtable_field_id TEXT NOT NULL PRIMARY KEY,   -- the link field (multipleRecordLinks)
    base_id TEXT NOT NULL,
    from_table_id TEXT NOT NULL,                   -- the table the link field is on
    to_table_id TEXT NOT NULL,                     -- the table it links to (airtable_fields.linked_table_id)
    inverse_field_id TEXT,                         -- the paired field on the other table, when the schema reports one (NULL = one-way)
    cardinality TEXT CHECK(cardinality IN ('one', 'many')),   -- how many records this side may link (from prefers_single_record_link)
    is_primary INTEGER NOT NULL DEFAULT 1 CHECK(is_primary IN (0, 1)),   -- 1 = this side creates the relationship; 0 = the reverse view
    source TEXT NOT NULL DEFAULT 'schema' CHECK(source IN ('schema', 'manual')),
    approved_by TEXT,                              -- NULL until a person approves
    approved_at TIMESTAMP,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (airtable_field_id) REFERENCES airtable_fields(field_id),
    FOREIGN KEY (from_table_id) REFERENCES airtable_tables(table_id),
    FOREIGN KEY (to_table_id) REFERENCES airtable_tables(table_id)
);
CREATE INDEX IF NOT EXISTS idx_field_relationships_base ON field_relationships(base_id);
CREATE INDEX IF NOT EXISTS idx_field_relationships_inverse ON field_relationships(inverse_field_id);
