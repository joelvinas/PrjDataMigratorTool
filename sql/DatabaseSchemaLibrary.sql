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
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_airtable_fields_table ON airtable_fields(table_id);
CREATE INDEX idx_airtable_fields_linked ON airtable_fields(linked_table_id);

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

-- 4. Custom Form Structure & Section-Level Field Placement
CREATE TABLE form_sections (
    form_id TEXT NOT NULL,
    form_name TEXT NOT NULL,
    section_id TEXT NOT NULL,
    section_name TEXT NOT NULL,
    placement_order INTEGER NOT NULL,
    PRIMARY KEY (form_id, section_id)
);

CREATE TABLE form_fields (
    form_id TEXT NOT NULL,
    section_id TEXT NOT NULL,
    airtable_field_id TEXT NOT NULL,
    workfront_dev_field_id TEXT,
    workfront_prod_field_id TEXT,
    field_order INTEGER NOT NULL,
    is_required INTEGER DEFAULT 0,
    PRIMARY KEY (form_id, section_id, airtable_field_id),
    FOREIGN KEY (form_id, section_id) REFERENCES form_sections(form_id, section_id)
);

-- 5. Automation Catalog & Action Steps (Airtable -> Workfront Fusion)
CREATE TABLE automation_catalog (
    automation_id TEXT PRIMARY KEY,
    automation_name TEXT NOT NULL,
    source_system TEXT DEFAULT 'Airtable',
    trigger_type TEXT NOT NULL,
    target_fusion_scenario_name TEXT,
    notes TEXT
);

CREATE TABLE automation_steps (
    step_id INTEGER PRIMARY KEY AUTOINCREMENT,
    automation_id TEXT NOT NULL,
    step_order INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    referenced_airtable_field_id TEXT,
    referenced_workfront_dev_field_id TEXT,
    referenced_workfront_prod_field_id TEXT,
    step_logic_description TEXT,
    FOREIGN KEY (automation_id) REFERENCES automation_catalog(automation_id)
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
