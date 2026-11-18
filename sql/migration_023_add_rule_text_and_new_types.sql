-- Migration 023 (DRAFT, not applied): the transformation rule text, four more Airtable types, and the owner's reference values. Adds ONE column to ONE table
-- (data_type_defaults.rule_text) and changes only rows still marked updated_by = 'seed', so a default a person edited is never overwritten. Safe to run again once
-- the column exists: ADD COLUMN cannot be repeated, so sql/apply_migrations.py checks PRAGMA table_info first (once this migration is folded in).
-- Run migrations 021 and 022 first.
--
-- Source: the owner's reference mapping (Airtable field type to Workfront Core and Planning types), conformed to the strict lists in src/datatypes.py:
--   * a Planning value that is not one of the 17 types ('No equivalent') is stored as NULL: no default, a person decides;
--   * a Core dataType and displayType that are not both on their lists (CALC, WFNATIVE, DOCUMENT, INTRNL, MULTINTRNL, ROLLUP, TYAH, RICH as a displayType) are stored as NULL;
--     the reference's wording is kept in rule_text;
--   * multilineText stays TEXT/TXTA (a text area the API can create); a true RICH field is made by hand in the Custom Form interface (owner verified);
--   * the defaults already seeded for types the reference lacks (dateTime, duration, percent, multipleCollaborators) are kept.
ALTER TABLE data_type_defaults ADD COLUMN rule_text TEXT;

-- Four types the reference adds.
INSERT OR IGNORE INTO data_type_defaults(system, airtable_type, default_type, default_display_type, note) VALUES
    ('planning', 'aiText', 'text', NULL, NULL),
    ('core', 'aiText', NULL, NULL, 'Create by hand in the Custom Form interface: the API cannot create a RICH field'),
    ('planning', 'manualSort', NULL, NULL, 'No Planning equivalent; Airtable internal system field, typically omitted'),
    ('core', 'manualSort', 'NMBR', 'TEXT', NULL),
    ('planning', 'multipleLookupValues', 'multi-select', NULL, NULL),
    ('core', 'multipleLookupValues', NULL, NULL, 'Reference lists TEXT/INTRNL, which the loader cannot create; derived or native'),
    ('planning', 'rollup', 'formula', NULL, NULL),
    ('core', 'rollup', NULL, NULL, 'Reference lists CALC/ROLLUP, which the loader cannot create; derived or native');

-- Planning values from the reference where the earlier seed had no default.
UPDATE data_type_defaults SET default_type='number', note='Numeric sequence; consider record-id if it is used as a business key' WHERE system='planning' AND airtable_type='autoNumber' AND default_type IS NULL AND updated_by='seed';
UPDATE data_type_defaults SET default_type='reference', note='No native attachment field; migrate files separately and keep references' WHERE system='planning' AND airtable_type='multipleAttachments' AND default_type IS NULL AND updated_by='seed';
UPDATE data_type_defaults SET note='TEXT/TXTA is a text area and the API can create it. A true rich text field (dataType RICH) cannot be created through the API: create it by hand in the Custom Form interface.' WHERE system='core' AND airtable_type='multilineText' AND updated_by='seed';
UPDATE data_type_defaults SET note='Create by hand in the Custom Form interface: the API cannot create a RICH field' WHERE system='core' AND airtable_type='richText' AND updated_by='seed';

-- The reference's transformation rule for every type it covers (plain text, shown on the Data types page).
UPDATE data_type_defaults SET rule_text='Copy generated content into a Rich Text field; strip unsupported AI metadata' WHERE system='core' AND airtable_type='aiText' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='AI-generated text content; preserve as standard text.' WHERE system='planning' AND airtable_type='aiText' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Create calculated or custom numbering logic; alternatively store imported value in Number field' WHERE system='core' AND airtable_type='autoNumber' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Numeric sequence IDs. Consider also storing as record-id if used as a business key.' WHERE system='planning' AND airtable_type='autoNumber' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Map TRUE/FALSE values directly' WHERE system='core' AND airtable_type='checkbox' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Direct mapping (TRUE / FALSE)' WHERE system='planning' AND airtable_type='checkbox' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Map to native Entered By field if available; otherwise store username/email in Text field' WHERE system='core' AND airtable_type='createdBy' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='System-generated creator reference.' WHERE system='planning' AND airtable_type='createdBy' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Map to native Entry Date field if available; otherwise preserve in Date field' WHERE system='core' AND airtable_type='createdTime' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='System-generated creation timestamp.' WHERE system='planning' AND airtable_type='createdTime' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Convert to numeric currency value, removing symbols and formatting' WHERE system='core' AND airtable_type='currency' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Direct mapping. Validate currency precision and locale formatting.' WHERE system='planning' AND airtable_type='currency' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Convert to Workfront date format' WHERE system='core' AND airtable_type='date' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Direct mapping. Preserve date/time behavior if Airtable field includes time.' WHERE system='planning' AND airtable_type='date' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Copy email address as-is' WHERE system='core' AND airtable_type='email' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='No dedicated email type exists in Workfront Planning. Store as text and optionally validate format during migration.' WHERE system='planning' AND airtable_type='email' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Recreate formula using Workfront calculated field syntax' WHERE system='core' AND airtable_type='formula' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Direct mapping if formula logic can be replicated in Workfront Planning. Review function compatibility.' WHERE system='planning' AND airtable_type='formula' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Map to native Last Updated By field if available; otherwise store as Text' WHERE system='core' AND airtable_type='lastModifiedBy' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='System-generated modifier reference.' WHERE system='planning' AND airtable_type='lastModifiedBy' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Map to native Last Update Date if available; otherwise store as Date/Time' WHERE system='core' AND airtable_type='lastModifiedTime' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='System-generated last modified timestamp.' WHERE system='planning' AND airtable_type='lastModifiedTime' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Preserve sort order value in a Number field' WHERE system='core' AND airtable_type='manualSort' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Airtable internal system field. Typically omitted during migration.' WHERE system='planning' AND airtable_type='manualSort' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Copy content preserving line breaks' WHERE system='core' AND airtable_type='multilineText' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Direct mapping.' WHERE system='planning' AND airtable_type='multilineText' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Migrate files separately and populate document references' WHERE system='core' AND airtable_type='multipleAttachments' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='No native attachment field. Typically migrate as references to assets, documents, or external URLs.' WHERE system='planning' AND airtable_type='multipleAttachments' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Create lookup relationship and repopulate values through referenced records' WHERE system='core' AND airtable_type='multipleLookupValues' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='If lookup values are simple text values. If lookup contains linked records, use reference instead' WHERE system='planning' AND airtable_type='multipleLookupValues' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Import records first, then establish linked-record relationships' WHERE system='core' AND airtable_type='multipleRecordLinks' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Direct conceptual match to linked records. May require multi-reference support in implementation.' WHERE system='planning' AND airtable_type='multipleRecordLinks' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Map values to predefined multi-select options' WHERE system='core' AND airtable_type='multipleSelects' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Direct mapping.' WHERE system='planning' AND airtable_type='multipleSelects' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Copy value directly; validate decimal precision' WHERE system='core' AND airtable_type='number' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Direct mapping.' WHERE system='planning' AND airtable_type='number' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Normalize formatting and store as text' WHERE system='core' AND airtable_type='phoneNumber' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='No dedicated phone field. Store as text.' WHERE system='planning' AND airtable_type='phoneNumber' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Convert to numeric rating scale' WHERE system='core' AND airtable_type='rating' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Preserve rating value (typically 1-5, 1-10, etc.).' WHERE system='planning' AND airtable_type='rating' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Copy formatting where supported; otherwise preserve as plain rich text' WHERE system='core' AND airtable_type='richText' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Formatting may be lost if Workfront Planning does not support equivalent rich text markup.' WHERE system='planning' AND airtable_type='richText' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Recreate as rollup, calculated field, or reporting aggregation depending on business logic' WHERE system='core' AND airtable_type='rollup' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Closest equivalent. May require recreation using Planning formulas or automation logic.' WHERE system='planning' AND airtable_type='rollup' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Match Airtable user email to Workfront user record' WHERE system='core' AND airtable_type='singleCollaborator' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Direct mapping to Workfront user reference.' WHERE system='planning' AND airtable_type='singleCollaborator' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Copy value as-is' WHERE system='core' AND airtable_type='singleLineText' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Direct mapping.' WHERE system='planning' AND airtable_type='singleLineText' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Map source values to predefined dropdown choices' WHERE system='core' AND airtable_type='singleSelect' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Direct mapping.' WHERE system='planning' AND airtable_type='singleSelect' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='Copy URL as-is; optionally validate format' WHERE system='core' AND airtable_type='url' AND updated_by='seed';
UPDATE data_type_defaults SET rule_text='No dedicated URL type. Store as text and optionally validate URL format.' WHERE system='planning' AND airtable_type='url' AND updated_by='seed';
