-- Migration 014: approved select option names. Adds ONE table: option_names.
-- Additive only: no existing table or column is changed. Safe to run more than once.
-- PROPOSED, NOT YET APPLIED, and NOT YET AUTHORISED: it is one of three ways to store an approved option name (see docs/LOADER_PLAN.md);
-- the owner has not chosen yet. Not in DatabaseSchemaLibrary.sql, the embedded DDL in server.py or sql/apply_migrations.py.

-- Option names follow the Core label rules. The tool proposes a repair for a name that breaks them; a person approves it here, with who,
-- when and why. The Airtable option is identified by its stable choice ID (airtable_fields.choices_json), never by its name. An option with
-- no approved row, whose name breaks the rules, stays on hold in the load plan. No record values are stored.
CREATE TABLE IF NOT EXISTS option_names (
    airtable_field_id TEXT NOT NULL,
    choice_id TEXT NOT NULL,          -- the Airtable select option's ID
    approved_name TEXT NOT NULL,      -- the name to create in Workfront
    approved_by TEXT NOT NULL,        -- initials or a short name (the app has no logins)
    approved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason TEXT,
    PRIMARY KEY (airtable_field_id, choice_id),
    FOREIGN KEY (airtable_field_id) REFERENCES airtable_fields(field_id)
);
