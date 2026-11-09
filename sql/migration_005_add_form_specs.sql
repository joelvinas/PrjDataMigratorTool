-- Migration 005: Stage 2 form recorder. Adds four tables (form_specs, form_items, form_logic, form_links).
-- The placeholder tables form_sections and form_fields are dropped by sql/apply_migrations.py, and only when they are empty.
-- Safe to run more than once. (python sql/apply_migrations.py applies this for you.)

CREATE TABLE IF NOT EXISTS form_specs (
    spec_id INTEGER PRIMARY KEY AUTOINCREMENT,
    form_id TEXT NOT NULL,          -- the form key: an Airtable form view ID, or a name you chose
    version_no INTEGER NOT NULL,    -- 1, 2, 3 ... one row per saved version, never edited
    form_name TEXT NOT NULL,
    base_id TEXT,
    table_id TEXT,
    view_id TEXT,                   -- the Airtable form view this was recorded from, if any
    version_note TEXT,
    spec_json TEXT NOT NULL,        -- the whole recorded form (the audit record). The tables below are rebuilt from it
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (form_id, version_no)
);
CREATE INDEX IF NOT EXISTS idx_form_specs_form ON form_specs(form_id);

CREATE TABLE IF NOT EXISTS form_items (
    spec_id INTEGER NOT NULL,
    item_id TEXT NOT NULL,
    parent_item_id TEXT,            -- the containing section, NULL at the top level
    item_order INTEGER NOT NULL,    -- position in document order across the whole form
    path TEXT NOT NULL,             -- for example 2.1.3 (1-based, among siblings)
    item_type TEXT NOT NULL CHECK(item_type IN ('section', 'field', 'hidden_field', 'heading', 'help_text', 'text_block')),
    title TEXT,                     -- section title, heading text, or a field's custom label
    content_md TEXT,                -- section description, field help text, help text or text block (Markdown)
    airtable_field_id TEXT,         -- field and hidden_field items
    is_required INTEGER DEFAULT 0,
    preset_value TEXT,              -- hidden_field: the value the form link presets
    preset_source TEXT,             -- hidden_field: where the value comes from (URL parameter, default, ...)
    PRIMARY KEY (spec_id, item_id),
    FOREIGN KEY (spec_id) REFERENCES form_specs(spec_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_form_items_field ON form_items(airtable_field_id);

CREATE TABLE IF NOT EXISTS form_logic (
    spec_id INTEGER NOT NULL,
    owner_item_id TEXT NOT NULL,    -- the section or item the rule belongs to
    logic_kind TEXT NOT NULL CHECK(logic_kind IN ('display', 'skip')),
    junction TEXT NOT NULL CHECK(junction IN ('AND', 'OR')),
    cond_order INTEGER NOT NULL,
    controlling_field_id TEXT NOT NULL,
    operator TEXT NOT NULL,
    value TEXT,
    skip_to_item_id TEXT,           -- skip rules only: a later section's item_id, 'END', or NULL for just skipping this section
    PRIMARY KEY (spec_id, owner_item_id, logic_kind, cond_order),
    FOREIGN KEY (spec_id) REFERENCES form_specs(spec_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_form_logic_field ON form_logic(controlling_field_id);

CREATE TABLE IF NOT EXISTS form_links (
    spec_id INTEGER NOT NULL,
    item_id TEXT NOT NULL,          -- 'form' for the form description
    text_role TEXT NOT NULL,        -- description, help or content
    ordinal INTEGER NOT NULL,       -- order of the link inside that text
    link_text TEXT NOT NULL,        -- what the person sees
    url TEXT NOT NULL,              -- the real address, verbatim
    PRIMARY KEY (spec_id, item_id, text_role, ordinal),
    FOREIGN KEY (spec_id) REFERENCES form_specs(spec_id) ON DELETE CASCADE
);
