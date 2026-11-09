-- Migration 006: Stage 3 automation recorder. Adds five tables (automation_specs, automation_items, automation_logic,
-- automation_field_refs, automation_links).
-- The placeholder tables automation_catalog and automation_steps are dropped by sql/apply_migrations.py, and only when they are empty.
-- Safe to run more than once. (python sql/apply_migrations.py applies this for you.)

CREATE TABLE IF NOT EXISTS automation_specs (
    spec_id INTEGER PRIMARY KEY AUTOINCREMENT,
    automation_id TEXT NOT NULL,    -- the automation key: a name-based key you chose
    version_no INTEGER NOT NULL,    -- 1, 2, 3 ... one row per saved version, never edited
    automation_name TEXT NOT NULL,
    base_id TEXT,
    trigger_type TEXT,
    target_scenario_name TEXT,      -- the Workfront Fusion scenario this will become
    version_note TEXT,
    spec_json TEXT NOT NULL,        -- the whole recorded automation (the audit record). The tables below are rebuilt from it
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (automation_id, version_no)
);
CREATE INDEX IF NOT EXISTS idx_automation_specs_key ON automation_specs(automation_id);

CREATE TABLE IF NOT EXISTS automation_items (
    spec_id INTEGER NOT NULL,
    item_id TEXT NOT NULL,          -- 'trigger', or the step or branch id within the spec
    parent_item_id TEXT,            -- the containing step (for a branch) or branch/repeat/NULL (for a step)
    item_order INTEGER NOT NULL,    -- position in document order across the whole automation (the trigger is 0)
    path TEXT NOT NULL,             -- 0 for the trigger, then for example 2, 2.1, 2.1.3
    item_kind TEXT NOT NULL CHECK(item_kind IN ('trigger', 'step', 'branch')),
    step_type TEXT,                 -- trigger type, action type, 'conditional', 'repeat' or 'branch'
    title TEXT,
    content_md TEXT,                -- the description (Markdown)
    table_id TEXT,                  -- the Airtable table the item works on, if any
    record_source TEXT,             -- update steps: which earlier step's record is updated (NULL = the trigger record)
    PRIMARY KEY (spec_id, item_id),
    FOREIGN KEY (spec_id) REFERENCES automation_specs(spec_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS automation_logic (
    spec_id INTEGER NOT NULL,
    owner_item_id TEXT NOT NULL,    -- the trigger, find step or branch the condition group belongs to
    node_path TEXT NOT NULL,        -- position in the nested group: 0 is the root group, 0.1 its second item, 0.1.0 and so on
    node_kind TEXT NOT NULL CHECK(node_kind IN ('group', 'condition')),
    junction TEXT,                  -- groups: AND or OR
    controlling_field_id TEXT,      -- conditions on an Airtable field
    source_item_id TEXT,            -- which record the field is read from: NULL = the trigger record, else an earlier step
    left_text TEXT,                 -- conditions on something that is not a field (a step output, a date, ...)
    operator TEXT,
    value TEXT,
    PRIMARY KEY (spec_id, owner_item_id, node_path),
    FOREIGN KEY (spec_id) REFERENCES automation_specs(spec_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_automation_logic_field ON automation_logic(controlling_field_id);

CREATE TABLE IF NOT EXISTS automation_field_refs (
    spec_id INTEGER NOT NULL,
    item_id TEXT NOT NULL,
    ref_order INTEGER NOT NULL,
    field_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('condition', 'set', 'read', 'use', 'watch')),
    detail TEXT,                    -- the value a field is set to, or how it is used
    PRIMARY KEY (spec_id, item_id, ref_order),
    FOREIGN KEY (spec_id) REFERENCES automation_specs(spec_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_automation_field_refs_field ON automation_field_refs(field_id);

CREATE TABLE IF NOT EXISTS automation_links (
    spec_id INTEGER NOT NULL,
    item_id TEXT NOT NULL,          -- 'automation' for the automation description
    text_role TEXT NOT NULL,        -- description, or setting:<name>
    ordinal INTEGER NOT NULL,       -- order of the link inside that text
    link_text TEXT NOT NULL,        -- what the person sees
    url TEXT NOT NULL,              -- the real address, verbatim
    PRIMARY KEY (spec_id, item_id, text_role, ordinal),
    FOREIGN KEY (spec_id) REFERENCES automation_specs(spec_id) ON DELETE CASCADE
);
