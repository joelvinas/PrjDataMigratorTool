-- Migration 020 (approved and applied by the owner; folded into sql/DatabaseSchemaLibrary.sql, the DDL embedded in server.py and sql/apply_migrations.py): where each table's Core objects are created. Adds TWO tables: table_core_parents and table_core_events.
-- Additive only. Safe to run more than once. Holds table IDs, Workfront object codes and IDs, who, when and why. No record value, no secret.
--
-- Why: table_core_objects (migration 011) says what a table BECOMES in Workfront Core (project, request or task). The Core API also needs to know where each
-- new object goes: its PARENT object type and parent object ID. That differs per table (there may be dozens of parent projects), so it is a decision stored
-- here per table and never a setting in config.json.
--   parent_obj_code   the parent's object code, as plain text (in Workfront: PROJ, PRGM, PORT or TASK)
--   parent_obj_id     the parent's ID, as plain text. Workfront IDs are 32-character hexadecimal GUIDs, but the column is TEXT with no format rule, so the same
--                     table can hold another system's IDs later without a schema change.
-- What the application checks for Workfront Core (not the database, so the rules can change without a migration):
--   a project (PROJ)  has a program (PRGM) or a portfolio (PORT) as parent, or none (both columns NULL = "decided: no parent");
--   a request (OPTASK) has the Request Queue project that holds it (PROJ) as parent;
--   a task (TASK)     has a project (PROJ) or another task (TASK) as parent.
-- The tool proposes; a person decides. Nothing is decided silently, and the loader refuses a table with no decision.
CREATE TABLE IF NOT EXISTS table_core_parents (
    table_id TEXT NOT NULL PRIMARY KEY,
    base_id TEXT NOT NULL,
    parent_obj_code TEXT,
    parent_obj_id TEXT,
    decided_by TEXT NOT NULL,                    -- initials or a short name (the app has no logins)
    decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason TEXT,
    CHECK ((parent_obj_code IS NULL) = (parent_obj_id IS NULL)),
    FOREIGN KEY (table_id) REFERENCES airtable_tables(table_id)
);
CREATE INDEX IF NOT EXISTS idx_table_core_parents_base ON table_core_parents(base_id);

-- Append-only history of both decisions (the object type in table_core_objects, and the parent above), so a changed or cleared decision keeps what it was
-- (the lesson of migrations 018 and 019).
CREATE TABLE IF NOT EXISTS table_core_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_id TEXT NOT NULL,
    base_id TEXT NOT NULL,
    subject TEXT NOT NULL CHECK(subject IN ('object_type', 'parent')),
    event_type TEXT NOT NULL CHECK(event_type IN ('decide', 'change', 'clear')),
    old_value TEXT,                              -- NULL when there was none before; for a parent: 'PROJ:<id>'
    new_value TEXT,                              -- NULL after a clear
    actor TEXT NOT NULL,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (table_id) REFERENCES airtable_tables(table_id)
);
CREATE INDEX IF NOT EXISTS idx_table_core_events_table ON table_core_events(table_id, event_id);
