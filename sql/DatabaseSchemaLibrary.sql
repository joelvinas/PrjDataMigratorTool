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
    core_target_type TEXT,            -- the Workfront Core data type (migration 012); target_type is the Planning data type
    core_display_type TEXT,           -- the Workfront Core displayType (migration 022); core_target_type is the dataType
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

-- 12. The load ID map (migration 010: Workfront IDs against the loader's temporary IDs, per stage)
-- Every item the loader creates in Workfront (a Planning record type or field, a Core custom field, a select option) has a TEMPORARY ID that
-- depends only on the Airtable IDs (see loadplan.py), such as P-F-fldXXXX. The moment a create call returns, its Workfront ID is written here
-- against the temporary ID, and a restart skips every row that already has one, so nothing is ever created twice. One row per temporary ID
-- per stage: Core has four environments; Workfront Planning has NO environments, only Workspaces (owner, 14 November 2026): everything goes into
-- one Workspace treated as isolated for Dev, which is then either wiped and recreated as a UAT Workspace or promoted to UAT. So 'environment' is the
-- stage label ('sandbox1', 'sandbox2', 'preview' and 'production' for Core, 'dev' and 'uat' for Planning) and workspace_id names the Planning Workspace the IDs belong to.
-- Promoting Dev to UAT relabels that Workspace's rows (a person's recorded decision); a wipe and recreate makes new rows. Each stage has its own IDs. Option rows (kind 'option') are rows
-- like any other, with their position in Airtable's order, so every select field's options are recorded by ID in order.
-- Every create call's result is ALSO logged against its temporary ID in runbook_logs (failing_component = the temporary ID), the existing log.
-- Nothing here holds a credential or a record value.
CREATE TABLE load_id_map (
    temp_id TEXT NOT NULL,            -- for example P-T-tblXXXX, P-F-fldXXXX, C-F-fldXXXX, P-O-fldXXXX-selXXXX
    environment TEXT NOT NULL CHECK(environment IN ('sandbox1', 'sandbox2', 'preview', 'production', 'dev', 'uat')),
    workspace_id TEXT,                -- the Planning Workspace these IDs belong to (NULL for Core)
    workspace_key TEXT NOT NULL DEFAULT '' CHECK(workspace_key = COALESCE(workspace_id, '')),   -- part of the key: the Workspace ID, or '' for Core (migration 016)
    system TEXT NOT NULL CHECK(system IN ('planning', 'core')),
    kind TEXT NOT NULL CHECK(kind IN ('record_type', 'form', 'field', 'option')),
    base_id TEXT NOT NULL,
    table_id TEXT,                    -- the Airtable table the item belongs to
    airtable_field_id TEXT,           -- the Airtable field (NULL for a record type or form, and for fields the migration adds itself)
    parent_temp_id TEXT,              -- the record type (for a Planning field) or the field (for an option)
    position INTEGER,                 -- an option's position in Airtable's order
    name TEXT NOT NULL,               -- the name sent to Workfront
    workfront_id TEXT,                -- NULL until the create call returns; once set, the item is never created again
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'created', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,                  -- a short message with any credential removed
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (temp_id, environment, workspace_key)
);
CREATE INDEX idx_load_id_map_base ON load_id_map(base_id, environment);
CREATE INDEX idx_load_id_map_field ON load_id_map(airtable_field_id);

-- 13. The Core object type of each table (migration 011)
-- A table that has a Workfront Core presence (table_targets.target = 'planning_and_core') becomes ONE kind of Core object: a project, a request
-- or a task. That is a human decision, recorded with who, when and why. A table with no row has no Core object type yet, and the loader keeps
-- its Core form on hold until it does.
CREATE TABLE table_core_objects (
    table_id TEXT NOT NULL PRIMARY KEY,
    base_id TEXT NOT NULL,
    core_object_type TEXT NOT NULL CHECK(core_object_type IN ('project', 'request', 'task')),
    decided_by TEXT NOT NULL,         -- initials or a short name (the app has no logins)
    decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason TEXT,
    FOREIGN KEY (table_id) REFERENCES airtable_tables(table_id)
);
CREATE INDEX idx_table_core_objects_base ON table_core_objects(base_id);

-- 14. Relationships between tables, defined by the Airtable schema and stored (migration 013)
-- Relationships are DEFINED BY THE AIRTABLE SCHEMA (airtable_fields.linked_table_id and inverse_link_field_id) and are STORED here so the
-- loader and the reports read one decided answer. The tool proposes: one row per link field, with its paired field when the schema reports
-- one. Exactly one side of a two-way pair is the real Planning relationship (is_primary = 1); the other side is the reverse view. A person
-- approves (approved_by, approved_at); nothing is decided silently, and a row that is not approved keeps the loader's second pass on hold.
-- No record values are stored here.
CREATE TABLE field_relationships (
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
    suggested_type TEXT CHECK(suggested_type IN ('one_to_many', 'many_to_one', 'one_to_one', 'many_to_many')),   -- what the tool proposed from the schema (migration 015)
    relationship_type TEXT CHECK(relationship_type IN ('one_to_many', 'many_to_one', 'one_to_one', 'many_to_many')),   -- what a person chose; NULL until they do
    tiebreaker TEXT CHECK(tiebreaker IN ('this_first', 'other_first', 'automatic')),   -- one_to_one / many_to_many only: which table loads first ('automatic' = the load order decides)
    confirmed INTEGER NOT NULL DEFAULT 0 CHECK(confirmed IN (0, 1)),   -- 1 = a person confirmed the direction (approved_by and approved_at say who and when)
    derived INTEGER NOT NULL DEFAULT 0 CHECK(derived IN (0, 1)),       -- 1 = an automatic pair the confirmed order already settles (the tool, not a person)
    auto_set INTEGER NOT NULL DEFAULT 0 CHECK(auto_set IN (0, 1)),     -- 1 = set because its partner was chosen (shown as auto-set)
    FOREIGN KEY (airtable_field_id) REFERENCES airtable_fields(field_id),
    FOREIGN KEY (from_table_id) REFERENCES airtable_tables(table_id),
    FOREIGN KEY (to_table_id) REFERENCES airtable_tables(table_id)
);
CREATE INDEX idx_field_relationships_base ON field_relationships(base_id);
CREATE INDEX idx_field_relationships_inverse ON field_relationships(inverse_field_id);

-- 15. Approved select option names and the option IDs read back from Workfront (migration 014)
-- Option names follow the Core label rules. The tool proposes a repair for a name that breaks them; a person approves it here, with who, when
-- and why. The Airtable option is identified by its stable choice ID (airtable_fields.choices_json), never by its name. An option whose name
-- breaks the rules and has no approved row stays on hold in the load plan. No record values are stored.
CREATE TABLE option_names (
    airtable_field_id TEXT NOT NULL,
    choice_id TEXT NOT NULL,          -- the Airtable select option's ID
    approved_name TEXT NOT NULL,      -- the name to create in Workfront
    approved_by TEXT NOT NULL,        -- initials or a short name (the app has no logins)
    approved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason TEXT,
    PRIMARY KEY (airtable_field_id, choice_id),
    FOREIGN KEY (airtable_field_id) REFERENCES airtable_fields(field_id)
);

-- The option IDs PULLED BACK from Workfront, so what exists there can be compared with what was approved. Core: the option object (POPT, a
-- parameter option, per Workfront's API as reported by the community; not confirmed in Adobe's pages) of a custom field. Planning: the select
-- option ID of a Planning field, in a Workspace. IDs differ per stage, so a row is per Core environment or per Planning Workspace.
-- load_id_map (migration 010) records what the loader CREATED; this records what was READ BACK, with the name Workfront reports.
CREATE TABLE option_workfront_ids (
    airtable_field_id TEXT NOT NULL,
    choice_id TEXT NOT NULL,
    system TEXT NOT NULL CHECK(system IN ('core', 'planning')),
    environment TEXT NOT NULL CHECK(environment IN ('sandbox1', 'sandbox2', 'preview', 'production', 'dev', 'uat')),
    workspace_id TEXT NOT NULL DEFAULT '',   -- the Planning Workspace (empty for Core)
    workfront_option_id TEXT NOT NULL,       -- the Core POPT ID, or the Planning option ID
    workfront_name TEXT,                     -- the name Workfront reports for it
    position INTEGER,                        -- its position in Workfront's order
    pulled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (airtable_field_id, choice_id, system, environment, workspace_id),
    FOREIGN KEY (airtable_field_id) REFERENCES airtable_fields(field_id)
);
CREATE INDEX idx_option_workfront_ids_wf ON option_workfront_ids(system, workfront_option_id);

-- 16. The record ID map (migration 017)
-- Every Airtable record gets ONE surrogate ID (an integer assigned in sorted order, so runs are repeatable: no random UUID), and everything
-- else points to it. Holds IDs, statuses, file sizes and hashes ONLY: no record value, no file name, no link.
-- 1. The registry: one row per Airtable record, the three IDs stored once.
CREATE TABLE source_records (
    source_record_uid INTEGER PRIMARY KEY AUTOINCREMENT,
    base_id TEXT NOT NULL,
    table_id TEXT NOT NULL,
    record_id TEXT NOT NULL,                  -- the Airtable record ID (recXXXX)
    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (base_id, table_id, record_id),    -- the same record can never be registered twice
    FOREIGN KEY (table_id) REFERENCES airtable_tables(table_id)
);
CREATE INDEX idx_source_records_table ON source_records(base_id, table_id);

-- 2. Target mappings: one row per target record. Consolidation (several source records into one target) and fan-out (one source into several
-- targets) are just more rows against the same surrogate. workspace_key is the Planning Workspace ID, or '' for Core.
CREATE TABLE target_mappings (
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
CREATE INDEX idx_target_mappings_target ON target_mappings(system, environment, target_record_id);

-- 3. Pass-completed markers: a restart reads these to know what is done and never repeats it. One row per record, pass and stage.
CREATE TABLE record_pass_markers (
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
CREATE TABLE attachment_map (
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

-- 17. The history of link direction decisions (migration 018)
-- field_relationships keeps only the latest decision, so every confirm, change, clear and auto-set is also appended here: who, when, why and the old
-- and new choice. Append-only. Field IDs and choices only: no record value.
CREATE TABLE link_direction_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    airtable_field_id TEXT NOT NULL,              -- the link field the event is about
    base_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('confirm', 'change', 'clear', 'auto_set')),   -- auto_set = the partner of a field a person chose on
    old_relationship_type TEXT,                   -- NULL when there was no decision before
    old_tiebreaker TEXT,
    new_relationship_type TEXT,                   -- NULL after a clear
    new_tiebreaker TEXT,
    actor TEXT NOT NULL,                          -- initials or a short name (the app has no logins)
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (airtable_field_id) REFERENCES airtable_fields(field_id)
);
CREATE INDEX idx_link_direction_events_field ON link_direction_events(airtable_field_id, event_id);

-- 18. Field-writer decisions and their history (migration 019)
-- The matrix of who fills each field is computed each time; what a person DECIDES about a formula-only or automation-only field is kept here (who,
-- when, why), with an append-only history. Field IDs and choices only: no record value.
CREATE TABLE field_writer_decisions (
    airtable_field_id TEXT NOT NULL PRIMARY KEY,
    base_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('recompute', 'park', 'fusion_owned', 'go_stale', 'users_also_edit')),
    decided_by TEXT NOT NULL,                    -- initials or a short name (the app has no logins)
    decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason TEXT,
    FOREIGN KEY (airtable_field_id) REFERENCES airtable_fields(field_id)
);
CREATE INDEX idx_field_writer_decisions_base ON field_writer_decisions(base_id);

-- Append-only history, so a changed or cleared decision does not lose what it was (the lesson of migration 018).
CREATE TABLE field_writer_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    airtable_field_id TEXT NOT NULL,
    base_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('decide', 'change', 'clear')),
    old_decision TEXT,                           -- NULL when there was none before
    new_decision TEXT,                           -- NULL after a clear
    actor TEXT NOT NULL,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (airtable_field_id) REFERENCES airtable_fields(field_id)
);
CREATE INDEX idx_field_writer_events_field ON field_writer_events(airtable_field_id, event_id);

-- 19. Where each table's Core objects are created, and the history of that decision (migration 020)
-- table_core_objects says what a table BECOMES in Core (project, request or task); this says where each new object GOES: the parent's object code and
-- ID as plain text (Workfront IDs are 32-character hex GUIDs; that check belongs to the page, not the database). A person decides; nothing is automatic.
-- Codes and IDs only: no record value.
CREATE TABLE table_core_parents (
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
CREATE INDEX idx_table_core_parents_base ON table_core_parents(base_id);

-- Append-only history of both decisions (the object type in table_core_objects, and the parent above), so a changed or cleared decision keeps what it was
-- (the lesson of migrations 018 and 019).
CREATE TABLE table_core_events (
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
CREATE INDEX idx_table_core_events_table ON table_core_events(table_id, event_id);

-- 20. The data type mapping per system: the default mapping, who set each field's type and how, and the history (migration 021, with 022 and 023)
-- Planning and Core have different type lists, so each field has two answers (field_names.target_type = Planning, core_target_type + core_display_type = Core).
-- The default mapping rows are seeded by seed() in src/draftmigrations.py (from migrations 021 and 023); a person edits a default and every edit is kept in data_type_events.
-- The default mapping: one row per (system, Airtable field type). A person may edit a default; every edit goes to data_type_events.
-- NULL default_type = the tool proposes nothing and a person must decide.
CREATE TABLE data_type_defaults (
    system TEXT NOT NULL CHECK(system IN ('planning', 'core')),
    airtable_type TEXT NOT NULL,
    default_type TEXT,                           -- Planning type, or the Core dataType
    default_display_type TEXT,                   -- Core displayType; NULL for Planning
    note TEXT,
    rule_text TEXT,                              -- the transformation rule in words (migration 023)
    updated_by TEXT NOT NULL DEFAULT 'seed',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (system, airtable_type)
);

-- Who set each field's type for each system, and how: default (taken from the mapping), override (a person chose another), import (spreadsheet).
CREATE TABLE field_type_decisions (
    airtable_field_id TEXT NOT NULL,
    system TEXT NOT NULL CHECK(system IN ('planning', 'core')),
    chosen_type TEXT NOT NULL,
    chosen_display_type TEXT,                    -- Core only
    source TEXT NOT NULL CHECK(source IN ('default', 'override', 'import')),
    decided_by TEXT NOT NULL,                    -- initials or a short name (the app has no logins)
    decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason TEXT,
    PRIMARY KEY (airtable_field_id, system),
    FOREIGN KEY (airtable_field_id) REFERENCES airtable_fields(field_id)
);

-- Append-only history (the lesson of migrations 018 and 019). A bulk update shares one batch_id. Default edits have airtable_field_id = NULL.
-- 'clear' also records a value that was not on the allowed list and was removed (the old value is kept here, so nothing is lost).
CREATE TABLE data_type_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT,
    airtable_field_id TEXT,
    system TEXT NOT NULL CHECK(system IN ('planning', 'core')),
    airtable_type TEXT,                          -- set on default edits
    event_type TEXT NOT NULL CHECK(event_type IN ('set', 'change', 'clear', 'default_change')),
    old_type TEXT,
    old_display_type TEXT,
    new_type TEXT,
    new_display_type TEXT,
    source TEXT,
    actor TEXT NOT NULL,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_data_type_events_field ON data_type_events(airtable_field_id, event_id);

CREATE INDEX idx_data_type_events_batch ON data_type_events(batch_id);

-- 21. Lookup lists: select fields authorized to become a record type of their own, and the history (migration 024)
-- A lookup list: the name of the record type to be created in Planning. The name is a Planning name and follows the Planning naming rules (checked by the app).
CREATE TABLE lookup_lists (
    list_id INTEGER PRIMARY KEY AUTOINCREMENT,
    base_id TEXT NOT NULL,
    list_name TEXT NOT NULL,
    created_by TEXT NOT NULL,                    -- initials or a short name (the app has no logins)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason TEXT
);

-- The latest decision per select field: this field's options go to this list. A field with no row stays an ordinary select field.
-- Several fields may share one list.
CREATE TABLE field_lookup_decisions (
    airtable_field_id TEXT NOT NULL PRIMARY KEY,
    list_id INTEGER NOT NULL,
    decided_by TEXT NOT NULL,
    decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason TEXT,
    FOREIGN KEY (airtable_field_id) REFERENCES airtable_fields(field_id),
    FOREIGN KEY (list_id) REFERENCES lookup_lists(list_id)
);

-- Append-only history (the lesson of migrations 018 and 019): rows are never updated or deleted. A bulk change shares one batch_id.
CREATE TABLE lookup_list_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT,
    base_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('list_create', 'list_rename', 'list_delete', 'assign', 'unassign')),
    list_id INTEGER,
    airtable_field_id TEXT,
    old_list_id INTEGER,
    old_name TEXT,
    new_name TEXT,
    actor TEXT NOT NULL,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_lookup_list_events_field ON lookup_list_events(airtable_field_id, event_id);

CREATE INDEX idx_lookup_list_events_batch ON lookup_list_events(batch_id);

-- 22. The field results of a Git-R-Done run (migration 025)
CREATE TABLE emergency_field_results (
    result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,                  -- one Go run: E<UTC start as yyyymmddThhmmssZ>-<Airtable table ID>
    run_at TIMESTAMP NOT NULL,             -- when the run started (UTC)
    base_id TEXT NOT NULL,
    table_id TEXT NOT NULL,
    airtable_field_id TEXT NOT NULL,
    airtable_field_name TEXT NOT NULL,
    airtable_type TEXT NOT NULL,
    planning_name TEXT,                    -- the Planning name the naming rules suggest (a suggestion for the crosswalk; the name SENT is the Airtable name, or PRE- + it)
    core_name TEXT,                        -- the suggested Core name
    label_name TEXT,                       -- the suggested Core label
    attempted_planning_type TEXT,          -- the Planning type tried first
    outcome TEXT NOT NULL CHECK(outcome IN ('clean', 'pre-converted', 'skipped', 'failed')),
    planning_field_id TEXT,                -- the Planning field ID (also in load_id_map, against the Airtable field ID)
    error_code TEXT,                       -- Planning's error code for the refused attempt
    error_text TEXT,                       -- a short message with any credential removed
    max_length INTEGER,                    -- the longest value, in characters (a number only; the values themselves are never kept)
    cardinality INTEGER,                   -- from the profile (airtable_fields.cardinality)
    null_count INTEGER,
    total_records INTEGER,
    option_list TEXT,                      -- JSON array of the select options' names (schema metadata)
    formula_definition TEXT,               -- the formula, or the lookup or rollup definition in words
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (run_id, airtable_field_id)
);

CREATE INDEX idx_emergency_results_table ON emergency_field_results(base_id, table_id, run_at);

-- 23. The Core fields created by the Core fields in bulk page (migration 026)
CREATE TABLE core_field_crosswalk (
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
CREATE INDEX idx_core_field_crosswalk_env ON core_field_crosswalk(environment, airtable_field_id);

-- 24. The calculated fields page (migration 027): Airtable formulas translated into Workfront Core calculated fields. Metadata only (formula and expression text,
-- statuses, IDs, call outcomes). One Core field (PARAM) may be shared by several Airtable formula fields and placed on several custom forms (CTGY), each placement
-- (CTGYPA) with its own expression, so calc_formula_crosswalk is keyed by (environment, category_id, parameter_id).
-- One row per preflight run: what was chosen, who approved it, and the fingerprints used to detect drift before a push.
CREATE TABLE calc_preflight_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    environment TEXT NOT NULL CHECK(environment IN ('sandbox1', 'sandbox2', 'preview', 'production')),
    base_id TEXT NOT NULL,
    table_id TEXT NOT NULL,
    category_id TEXT NOT NULL,             -- the Core CTGY (custom form) ID chosen for this run
    category_name TEXT,                    -- its name as Workfront reported it, for display
    run_at TEXT NOT NULL,                  -- UTC
    run_by TEXT NOT NULL,
    schema_hash TEXT NOT NULL,             -- fingerprint of the table's formula fields (ID, formula text, result type) at preflight time
    crosswalk_hash TEXT NOT NULL,          -- fingerprint of the crosswalk rows the run used (core_field_crosswalk, calc_formula_crosswalk)
    state TEXT NOT NULL DEFAULT 'preflight' CHECK(state IN ('preflight', 'approved', 'pushing', 'pushed', 'superseded')),
    approved_by TEXT,
    approved_at TEXT,
    approval_reason TEXT
);
CREATE INDEX idx_calc_preflight_runs_table ON calc_preflight_runs(environment, table_id, run_id);

-- One row per formula per run: the outcome of the preflight (the approved snapshot the push reads, and the audit trail).
CREATE TABLE calc_preflight_items (
    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    formula_field_id TEXT NOT NULL,        -- the Airtable field ID of the formula field
    status TEXT NOT NULL CHECK(status IN ('TRANSLATABLE', 'BLOCKED_MISSING_REFERENCE', 'BLOCKED_UNSUPPORTED_FUNCTION', 'BLOCKED_DEPENDENCY')),
    reason TEXT,
    source_formula TEXT,                   -- the Airtable formula as stored (the snapshot)
    referenced_field_ids TEXT,             -- JSON array
    functions_found TEXT,                  -- JSON array of distinct function names, sorted
    depends_on TEXT,                       -- JSON array: formula field IDs this one reads
    push_order INTEGER,                    -- dependency order, 1 first
    translated_expression TEXT,            -- the Workfront expression for THIS form (NULL when blocked)
    target_name TEXT,                      -- the Core field name (the API name; the shared field's name when several Airtable fields consolidate)
    target_label TEXT,
    workfront_data_type TEXT,              -- result type of the PARAM (set when the PARAM is created; a reused PARAM keeps its own)
    workfront_display_type TEXT,
    reuse_parameter_id TEXT,               -- the PARAM already in Core under target_name (shared field): the push does not create it, it only places it on this form
    FOREIGN KEY (run_id) REFERENCES calc_preflight_runs(run_id),
    UNIQUE (run_id, formula_field_id)
);

-- Durable record of every placement of a calculated field on a form: one row per (environment, form, field). This is what the crosswalk of migration 026 cannot say:
-- the same parameter_id appears on several forms, each with its own expression and its own CTGYPA.
CREATE TABLE calc_formula_crosswalk (
    placement_id INTEGER PRIMARY KEY AUTOINCREMENT,
    environment TEXT NOT NULL CHECK(environment IN ('sandbox1', 'sandbox2', 'preview', 'production')),
    category_id TEXT NOT NULL,             -- CTGY: the custom form this placement is on
    parameter_id TEXT NOT NULL,            -- PARAM: the (possibly shared) calculated field
    ctgypa_id TEXT,                        -- CTGYPA: the field-on-form association (NULL until the field is attached)
    airtable_field_id TEXT NOT NULL,       -- the Airtable formula field this placement came from
    airtable_table_id TEXT NOT NULL,
    workfront_name TEXT NOT NULL,
    expression TEXT,                       -- the expression last written to this CTGYPA (NULL until step 3)
    source_run_id INTEGER,                 -- the approved preflight run that produced it
    created_at TEXT,
    updated_at TEXT,
    UNIQUE (environment, category_id, parameter_id),
    UNIQUE (environment, category_id, airtable_field_id),
    FOREIGN KEY (source_run_id) REFERENCES calc_preflight_runs(run_id)
);
CREATE INDEX idx_calc_formula_crosswalk_param ON calc_formula_crosswalk(environment, parameter_id);

-- Push progress per item, so a retry touches only the failed ones. The IDs live in calc_formula_crosswalk once known.
CREATE TABLE calc_push_items (
    item_id INTEGER PRIMARY KEY,           -- the calc_preflight_items row
    step_done TEXT NOT NULL DEFAULT 'none' CHECK(step_done IN ('none', 'created', 'attached', 'expression_written', 'verified')),
    last_error TEXT,                       -- cleaned message, never a token
    updated_at TEXT,
    FOREIGN KEY (item_id) REFERENCES calc_preflight_items(item_id)
);

-- Every Workfront call the push makes (separate from api_call_log, which is the Airtable call log).
CREATE TABLE calc_push_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    item_id INTEGER,
    step TEXT NOT NULL,                    -- create_param, read_form, attach_param, read_association, write_expression, verify
    http_method TEXT NOT NULL,
    endpoint TEXT NOT NULL,                -- path only, never headers or token
    sent_at TEXT NOT NULL,                 -- UTC
    received_at TEXT,
    http_status INTEGER,
    success INTEGER NOT NULL CHECK(success IN (0, 1)),
    error_message TEXT,                    -- cleaned
    FOREIGN KEY (run_id) REFERENCES calc_preflight_runs(run_id)
);
CREATE INDEX idx_calc_push_log_run ON calc_push_log(run_id, log_id);

-- 25. The list of valid custom forms of the calculated fields page (migration 028): the last accepted upload (category_id, name). Metadata only. One list for the whole tool.
CREATE TABLE calc_valid_forms (
    category_id TEXT PRIMARY KEY,          -- the Core CTGY ID
    name TEXT NOT NULL,                    -- the form's name as the uploaded list gives it
    position INTEGER NOT NULL,             -- the row order of the upload, so the list comes back in the same order
    uploaded_at TEXT NOT NULL,             -- UTC
    source_filename TEXT                   -- the name of the uploaded file, for display only
);
