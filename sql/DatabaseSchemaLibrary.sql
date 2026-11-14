-- 1. Airtable Schema & Metadata Tracking
CREATE TABLE airtable_fields (
    base_id TEXT NOT NULL,
    table_id TEXT NOT NULL,
    table_name TEXT NOT NULL,
    field_id TEXT NOT NULL PRIMARY KEY,
    field_name TEXT NOT NULL,
    data_type TEXT NOT NULL,
    linked_table_id TEXT,          -- populated only for multipleRecordLinks (options.linkedTableId)
    inverse_link_field_id TEXT,    -- optional: options.inverseLinkFieldId, the paired field on the linked table
    prefers_single_record_link INTEGER, -- 1/0 from options.prefersSingleRecordLink (link fields only), NULL otherwise
    row_count INTEGER DEFAULT 0,
    null_count INTEGER DEFAULT 0,
    cardinality INTEGER DEFAULT 0,
    cardinality_is_capped INTEGER DEFAULT 0, -- 1 = cardinality is a lower bound: more distinct values than the cap
    -- Field settings from the Metadata API (all NULL when not applicable or not reported)
    field_description TEXT,        -- the description typed on the field in Airtable
    is_primary_field INTEGER,      -- 1 = the table's primary field, 0 = not, NULL = not reported
    options_json TEXT,             -- the field's full options object exactly as returned (lossless backup of everything below)
    choices_json TEXT,             -- singleSelect / multipleSelects: [{"id","name","color"}] in Airtable's order
    formula_text TEXT,             -- formula fields: options.formula (and a rollup's formula when Airtable returns one)
    referenced_field_ids TEXT,     -- JSON array: options.referencedFieldIds (formula, rollup, lookup, lastModifiedTime)
    source_link_field_id TEXT,     -- rollup / lookup / count: options.recordLinkFieldId, the link field it reads through
    source_field_id TEXT,          -- rollup / lookup: options.fieldIdInLinkedTable, the field read in the linked table
    is_valid INTEGER,              -- formula / rollup / lookup / count: 1 = valid, 0 = broken (options.isValid)
    result_type TEXT,              -- computed fields: options.result.type (what the formula, rollup or lookup returns)
    result_options_json TEXT,      -- computed fields: options.result.options (precision, date format, choices of the result)
    number_precision INTEGER,      -- number / percent / currency: decimal places (options.precision)
    currency_symbol TEXT,          -- currency: options.symbol
    date_format TEXT,              -- date / dateTime: options.dateFormat.name (for example us, european, iso, local, friendly)
    time_format TEXT,              -- dateTime: options.timeFormat.name (12hour or 24hour)
    time_zone TEXT,                -- dateTime: options.timeZone
    duration_format TEXT,          -- duration: options.durationFormat (for example h:mm:ss)
    rating_max INTEGER,            -- rating: options.max
    icon TEXT,                     -- rating / checkbox: options.icon
    option_color TEXT,             -- rating / checkbox: options.color
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_airtable_fields_table ON airtable_fields(table_id);
CREATE INDEX idx_airtable_fields_linked ON airtable_fields(linked_table_id);

-- 1b. Table-level and view metadata from the Metadata API (no record data)
CREATE TABLE airtable_tables (
    table_id TEXT NOT NULL PRIMARY KEY,
    base_id TEXT NOT NULL,
    table_name TEXT NOT NULL,
    table_description TEXT,        -- the description typed on the table in Airtable
    primary_field_id TEXT,         -- the table's primaryFieldId
    table_json TEXT,               -- the table object as returned, minus its fields and views (lossless for anything else)
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_airtable_tables_base ON airtable_tables(base_id);

CREATE TABLE airtable_views (
    view_id TEXT NOT NULL PRIMARY KEY,
    base_id TEXT NOT NULL,
    table_id TEXT NOT NULL,
    view_name TEXT NOT NULL,
    view_type TEXT NOT NULL,       -- grid, form, calendar, gallery, kanban, timeline, block (Gantt), ...
    view_position INTEGER,         -- 0-based position in the order Airtable listed the table's views
    visible_field_ids TEXT,        -- JSON array of Airtable field IDs the view shows (requested with include=visibleFieldIds)
    view_json TEXT,                -- the view object exactly as returned (lossless backup of everything above)
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (table_id) REFERENCES airtable_tables(table_id)
);

CREATE INDEX idx_airtable_views_table ON airtable_views(table_id);
CREATE INDEX idx_airtable_views_type ON airtable_views(view_type);

-- 1c. Attachment size and type statistics (counts, bytes and file types only: no filenames, links or file contents)
CREATE TABLE airtable_attachment_stats (
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

CREATE INDEX idx_attachment_stats_table ON airtable_attachment_stats(table_id);
CREATE INDEX idx_attachment_stats_base ON airtable_attachment_stats(base_id);

-- 2. Workfront Planning Environment Schema (Dev & Prod)
CREATE TABLE workfront_fields (
    workfront_field_pk TEXT PRIMARY KEY, -- Composite key: environment + field_id
    environment TEXT CHECK(environment IN ('DEV', 'PROD')) NOT NULL,
    workspace_name TEXT NOT NULL,
    record_type TEXT NOT NULL,
    sub_record_type TEXT,
    field_id TEXT NOT NULL,
    field_name TEXT NOT NULL,
    data_type TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. Field & ID Crosswalk Mapping
CREATE TABLE field_crosswalk (
    crosswalk_id INTEGER PRIMARY KEY AUTOINCREMENT,
    airtable_base_id TEXT NOT NULL,
    airtable_table_name TEXT NOT NULL,
    airtable_field_id TEXT NOT NULL,
    airtable_field_name TEXT NOT NULL,
    workfront_dev_field_id TEXT,
    workfront_prod_field_id TEXT,
    mapping_notes TEXT,
    FOREIGN KEY (airtable_field_id) REFERENCES airtable_fields(field_id)
);

-- 4. Stage 2 Form Recorder (one versioned JSON spec per save, plus tables rebuilt from it for queries)
CREATE TABLE form_specs (
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
CREATE INDEX idx_form_specs_form ON form_specs(form_id);

CREATE TABLE form_items (
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
CREATE INDEX idx_form_items_field ON form_items(airtable_field_id);

CREATE TABLE form_logic (
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
CREATE INDEX idx_form_logic_field ON form_logic(controlling_field_id);

CREATE TABLE form_links (
    spec_id INTEGER NOT NULL,
    item_id TEXT NOT NULL,          -- 'form' for the form description
    text_role TEXT NOT NULL,        -- description, help or content
    ordinal INTEGER NOT NULL,       -- order of the link inside that text
    link_text TEXT NOT NULL,        -- what the person sees
    url TEXT NOT NULL,              -- the real address, verbatim
    PRIMARY KEY (spec_id, item_id, text_role, ordinal),
    FOREIGN KEY (spec_id) REFERENCES form_specs(spec_id) ON DELETE CASCADE
);

-- 5. Stage 3 Automation Recorder (one versioned JSON spec per save, plus tables rebuilt from it for queries)
CREATE TABLE automation_specs (
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
CREATE INDEX idx_automation_specs_key ON automation_specs(automation_id);

CREATE TABLE automation_items (
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

CREATE TABLE automation_logic (
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
CREATE INDEX idx_automation_logic_field ON automation_logic(controlling_field_id);

CREATE TABLE automation_field_refs (
    spec_id INTEGER NOT NULL,
    item_id TEXT NOT NULL,
    ref_order INTEGER NOT NULL,
    field_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('condition', 'set', 'read', 'use', 'watch')),
    detail TEXT,                    -- the value a field is set to, or how it is used
    PRIMARY KEY (spec_id, item_id, ref_order),
    FOREIGN KEY (spec_id) REFERENCES automation_specs(spec_id) ON DELETE CASCADE
);
CREATE INDEX idx_automation_field_refs_field ON automation_field_refs(field_id);

CREATE TABLE automation_links (
    spec_id INTEGER NOT NULL,
    item_id TEXT NOT NULL,          -- 'automation' for the automation description
    text_role TEXT NOT NULL,        -- description, or setting:<name>
    ordinal INTEGER NOT NULL,       -- order of the link inside that text
    link_text TEXT NOT NULL,        -- what the person sees
    url TEXT NOT NULL,              -- the real address, verbatim
    PRIMARY KEY (spec_id, item_id, text_role, ordinal),
    FOREIGN KEY (spec_id) REFERENCES automation_specs(spec_id) ON DELETE CASCADE
);

-- 6. Dependency Matrix & Data Ingestion Load Order
CREATE TABLE dependency_matrix (
    table_id TEXT PRIMARY KEY,
    table_name TEXT NOT NULL,
    parent_table_ids TEXT, -- Comma-separated or JSON list of required parent table IDs
    load_order_sequence INTEGER NOT NULL,
    dependency_depth INTEGER DEFAULT 0
);

-- 7. Audit Log & Migration Runbook
CREATE TABLE runbook_logs (
    error_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source_system TEXT NOT NULL,
    failing_component TEXT NOT NULL,
    error_message TEXT NOT NULL,
    root_cause_summary TEXT,
    mitigation_steps_taken TEXT,
    preventative_action TEXT,
    status TEXT CHECK(status IN ('OPEN', 'INVESTIGATING', 'RESOLVED', 'BYPASSED')) DEFAULT 'OPEN',
    verified_by TEXT
);

-- 8. API Call Log (every call, success or failure)
CREATE TABLE api_call_log (
    call_id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,   -- UTC
    source_system TEXT NOT NULL,                     -- 'Airtable' (later 'Workfront Planning')
    purpose TEXT NOT NULL,                           -- 'schema_fetch', 'profile_page', etc.
    http_method TEXT NOT NULL,
    endpoint TEXT NOT NULL,                          -- URL path only, never headers or token
    base_id TEXT,
    table_id TEXT,
    attempt INTEGER DEFAULT 1,                       -- retry number for the same request
    http_status INTEGER,                             -- NULL if no response (network error, timeout)
    success INTEGER NOT NULL CHECK(success IN (0, 1)),
    duration_ms INTEGER,
    records_returned INTEGER,
    error_message TEXT
);

CREATE INDEX idx_api_call_log_time ON api_call_log(timestamp);
CREATE INDEX idx_api_call_log_status ON api_call_log(success, http_status);

-- 9. Field and table name review (migration 007: the field name validator's saved decisions)
-- One row per Airtable field (keyed by the stable Airtable field ID): the names a person chose or imported, the approval state of each
-- target, the description and Workfront type, and the abandon decision. Names are validated per target (see fieldnames.py).
CREATE TABLE field_names (
    airtable_field_id TEXT NOT NULL PRIMARY KEY,
    planning_name TEXT,               -- Workfront Planning field name (NULL until imported or approved)
    core_name TEXT,                   -- Workfront Core custom field name
    core_label TEXT,                  -- Workfront Core custom field label
    planning_name_status TEXT NOT NULL DEFAULT 'unreviewed' CHECK(planning_name_status IN ('unreviewed', 'approved', 'needs_human')),
    core_name_status TEXT NOT NULL DEFAULT 'unreviewed' CHECK(core_name_status IN ('unreviewed', 'approved', 'needs_human')),
    core_label_status TEXT NOT NULL DEFAULT 'unreviewed' CHECK(core_label_status IN ('unreviewed', 'approved', 'needs_human')),
    description TEXT,                 -- generated from the lineage syntax, then hand-edited
    target_type TEXT,                 -- target Workfront data type
    planning_name_verdict TEXT,       -- cache of the last validation: PASS, AUTO_FIXABLE or NEEDS_HUMAN (always recomputable)
    core_name_verdict TEXT,
    core_label_verdict TEXT,
    verdict_rules TEXT,               -- JSON: rule IDs per target from the last validation
    validated_at TIMESTAMP,
    abandoned INTEGER NOT NULL DEFAULT 0,   -- 1 = a person abandoned this field (never automatic)
    abandoned_by TEXT,
    abandoned_at TIMESTAMP,
    abandoned_reason TEXT,
    import_batch TEXT,                -- which spreadsheet import last set the names
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (airtable_field_id) REFERENCES airtable_fields(field_id)
);
CREATE INDEX idx_field_names_abandoned ON field_names(abandoned);

-- One row per Airtable table: the new table name a person approved. The suggestion itself is always recomputed, never stored.
-- The table-name swap in field names reads this (approved name first, else the suggestion).
CREATE TABLE table_names (
    airtable_table_id TEXT NOT NULL PRIMARY KEY,
    new_table_name TEXT,
    status TEXT NOT NULL DEFAULT 'unreviewed' CHECK(status IN ('unreviewed', 'approved', 'needs_human')),
    approved_by TEXT,
    approved_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (airtable_table_id) REFERENCES airtable_tables(table_id)
);

-- Append-only audit trail of every human decision and of the settings in force (toggles and presets), so any run can be reproduced.
-- Rows are never updated or deleted. Current settings for a base = its newest 'settings' event. No record values are ever stored here.
CREATE TABLE name_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_type TEXT NOT NULL CHECK(subject_type IN ('field', 'table', 'settings')),
    subject_id TEXT NOT NULL,         -- Airtable field ID, Airtable table ID, or the base ID for settings
    event_type TEXT NOT NULL CHECK(event_type IN ('import', 'approve', 'reject', 'needs_human', 'abandon', 'unabandon', 'settings')),
    target TEXT,                      -- planning_name, core_name, core_label (NULL when it applies to the whole field or table)
    old_value TEXT,
    new_value TEXT,                   -- for 'settings': the JSON of the toggles and presets
    actor TEXT NOT NULL,              -- who decided (free text: the app has no logins)
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_name_events_subject ON name_events(subject_type, subject_id);

-- 10. Table scope and target decisions (migration 008: human decisions kept apart from the ingested table definitions)
-- Which tables are relevant is a human decision, not part of the ingested table definition, so it lives here and a schema re-ingest can
-- never overwrite it. A table with NO row is UNDECIDED, which is different from 'out'. The name validator and everything downstream run on
-- every table that is not explicitly 'out'.
CREATE TABLE table_scope (
    base_id TEXT NOT NULL,
    table_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('in', 'out')),
    decided_by TEXT NOT NULL,         -- initials or a short name (the app has no logins)
    decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason TEXT,
    PRIMARY KEY (base_id, table_id),
    FOREIGN KEY (table_id) REFERENCES airtable_tables(table_id)
);
CREATE INDEX idx_table_scope_decision ON table_scope(base_id, decision);

-- For each in-scope table: does it land in Workfront Planning only, or in Planning plus a Workfront Core presence (attachments, a front-facing
-- data entry form, or a defined major workflow)? A table with no row has no target decision yet.
CREATE TABLE table_targets (
    table_id TEXT NOT NULL PRIMARY KEY,
    base_id TEXT NOT NULL,
    target TEXT NOT NULL CHECK(target IN ('planning_only', 'planning_and_core')),
    decided_by TEXT NOT NULL,
    decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason TEXT,
    FOREIGN KEY (table_id) REFERENCES airtable_tables(table_id)
);
CREATE INDEX idx_table_targets_base ON table_targets(base_id);

-- 11. Field consolidation groups (migration 009: several fields of one table become one Workfront field)
-- A consolidation group is several Airtable fields of ONE table that will become ONE Workfront field. A person chooses the survivor (the
-- field whose name and settings carry forward), records why, and says what the loader should do later when two members are both filled
-- and differ. Merged members drop out of the name review and the duplicate checks, and the survivor's description lists every origin.
-- A group can be dissolved at any time (the rows are deleted; the decision history stays in name_events, which is append-only).
CREATE TABLE field_groups (
    group_id INTEGER PRIMARY KEY AUTOINCREMENT,
    base_id TEXT NOT NULL,
    table_id TEXT NOT NULL,
    survivor_field_id TEXT NOT NULL,
    conflict_rule TEXT NOT NULL DEFAULT 'review' CHECK(conflict_rule IN ('review', 'survivor_wins', 'first_filled')),
                                      -- for the loader, when two members are both filled and differ:
                                      -- 'review' = a person resolves each such record, 'survivor_wins' = keep the survivor's value,
                                      -- 'first_filled' = use the first non-empty value in member order
    reason TEXT NOT NULL,             -- why these fields are being consolidated
    decided_by TEXT NOT NULL,         -- initials or a short name (the app has no logins)
    decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (table_id) REFERENCES airtable_tables(table_id),
    FOREIGN KEY (survivor_field_id) REFERENCES airtable_fields(field_id)
);
CREATE INDEX idx_field_groups_table ON field_groups(base_id, table_id);

-- The members of each group, including the survivor. A field can be in at most one group (field_id is the primary key).
CREATE TABLE field_group_members (
    group_id INTEGER NOT NULL,
    field_id TEXT NOT NULL PRIMARY KEY,
    member_order INTEGER NOT NULL,    -- 0 = the survivor; the others in the order their origins are listed and 'first_filled' is applied
    FOREIGN KEY (group_id) REFERENCES field_groups(group_id),
    FOREIGN KEY (field_id) REFERENCES airtable_fields(field_id)
);
CREATE INDEX idx_field_group_members_group ON field_group_members(group_id);
