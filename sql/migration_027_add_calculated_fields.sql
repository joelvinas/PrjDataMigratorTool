-- Migration 027 (DRAFT, NOT APPLIED, NOT folded into DatabaseSchemaLibrary.sql, the embedded DDL in server.py or apply_migrations.py): the calculated fields page.
-- Adds FIVE tables. Additive only, safe to run more than once. Touches no existing table.
-- Holds metadata only: Airtable field IDs, formula text, the translated expression text, statuses, reasons, Workfront IDs, call outcomes. No record value, no credential,
-- no Authorization header, no request or response body.
--
-- Why: the page translates Airtable formula fields into Workfront Core calculated field expressions. A preflight (read-only) stores one row per formula; a person approves
-- the run; the push reads that stored snapshot (it never re-translates), refuses when the schema, the crosswalk or a formula changed since (the hashes below), and logs every
-- Workfront call in its own table, apart from api_call_log (the Airtable call log).
--
-- The shared-field model: several Airtable formula fields (same name, similar type, same purpose, on different tables) may become ONE Core field (one PARAM). That one
-- field is placed on several custom forms (CTGY), and each placement (a CTGYPA) carries its OWN expression. So the expression belongs to the placement, not to the field:
--   SalesPersonCommission (one PARAM)  on form SpecialOrderSales:  {DE:base_price}*0.15
--                                      on form InStoreSales:       IF({DE:total}<200, {DE:total}*0.03, {DE:total}*0.07)
-- The durable record of each placement is calc_formula_crosswalk, keyed by (environment, category_id, parameter_id), which is the key the crosswalk of migration 026 does not have.
--
-- Fold-in happens only after the owner confirms this migration is applied (CLAUDE.md).

-- One row per preflight run: what was chosen, who approved it, and the fingerprints used to detect drift before a push.
CREATE TABLE IF NOT EXISTS calc_preflight_runs (
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
CREATE INDEX IF NOT EXISTS idx_calc_preflight_runs_table ON calc_preflight_runs(environment, table_id, run_id);

-- One row per formula per run: the outcome of the preflight (the approved snapshot the push reads, and the audit trail).
CREATE TABLE IF NOT EXISTS calc_preflight_items (
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
CREATE TABLE IF NOT EXISTS calc_formula_crosswalk (
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
CREATE INDEX IF NOT EXISTS idx_calc_formula_crosswalk_param ON calc_formula_crosswalk(environment, parameter_id);

-- Push progress per item, so a retry touches only the failed ones. The IDs live in calc_formula_crosswalk once known.
CREATE TABLE IF NOT EXISTS calc_push_items (
    item_id INTEGER PRIMARY KEY,           -- the calc_preflight_items row
    step_done TEXT NOT NULL DEFAULT 'none' CHECK(step_done IN ('none', 'created', 'attached', 'expression_written', 'verified')),
    last_error TEXT,                       -- cleaned message, never a token
    updated_at TEXT,
    FOREIGN KEY (item_id) REFERENCES calc_preflight_items(item_id)
);

-- Every Workfront call the push makes (separate from api_call_log, which is the Airtable call log).
CREATE TABLE IF NOT EXISTS calc_push_log (
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
CREATE INDEX IF NOT EXISTS idx_calc_push_log_run ON calc_push_log(run_id, log_id);
