-- Migration 016 (PROPOSED, not applied): put the Planning Workspace in the key of load_id_map.
-- THIS IS THE ONE STEP THAT IS NOT PURELY ADDITIVE: SQLite cannot change a primary key, so the table is rebuilt (create the new one, copy the
-- rows, drop the old one, rename). It is safe only because nothing reads or writes load_id_map yet: apply it ONLY while the table is empty,
-- and apply_migrations.py must refuse (and report) if it has rows. Back the database up first. Run once (the runner checks PRAGMA table_info for
-- the workspace_key column before running it).
--
-- Why: the key was (temp_id, environment). Planning has no environments, only Workspaces: Dev is wiped and recreated as UAT, or promoted. A wipe and
-- recreate reuses the same temporary IDs and the same stage label ('dev'), so the first run's rows would collide with the second's. The key becomes
-- (temp_id, environment, workspace_key): workspace_key is the Planning Workspace ID, or '' for Core (a key part cannot be NULL).
CREATE TABLE load_id_map_v2 (
    temp_id TEXT NOT NULL,
    environment TEXT NOT NULL CHECK(environment IN ('sandbox1', 'sandbox2', 'preview', 'production', 'dev', 'uat')),
    workspace_id TEXT,
    workspace_key TEXT NOT NULL DEFAULT '' CHECK(workspace_key = COALESCE(workspace_id, '')),
    system TEXT NOT NULL CHECK(system IN ('planning', 'core')),
    kind TEXT NOT NULL CHECK(kind IN ('record_type', 'form', 'field', 'option')),
    base_id TEXT NOT NULL,
    table_id TEXT,
    airtable_field_id TEXT,
    parent_temp_id TEXT,
    position INTEGER,
    name TEXT NOT NULL,
    workfront_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'created', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (temp_id, environment, workspace_key)
);
INSERT INTO load_id_map_v2 (temp_id, environment, workspace_id, workspace_key, system, kind, base_id, table_id, airtable_field_id, parent_temp_id, position, name,
                            workfront_id, status, attempts, last_error, created_at, updated_at)
    SELECT temp_id, environment, workspace_id, COALESCE(workspace_id, ''), system, kind, base_id, table_id, airtable_field_id, parent_temp_id, position, name,
           workfront_id, status, attempts, last_error, created_at, updated_at FROM load_id_map;
DROP TABLE load_id_map;
ALTER TABLE load_id_map_v2 RENAME TO load_id_map;
CREATE INDEX idx_load_id_map_base ON load_id_map(base_id, environment);
CREATE INDEX idx_load_id_map_field ON load_id_map(airtable_field_id);
