# Field naming rules

These rules are applied by the migration's field name validator. Every proposed change is a suggestion that a person approves; the tool never renames anything on its own.

## Targets

- **Workfront Planning field name** (`planning_name`): strict allowlist of ASCII letters, digits, spaces and hyphens. Maximum length 250, confirmed (Adobe Workfront Planning, Field overview: 250 characters).
- **Workfront Core custom field name** (`core_name`): strict allowlist of ASCII letters, digits, spaces and hyphens. Maximum length 255, PROVISIONAL (Adobe's custom form page states no length limit for API names; 255 is a placeholder).
- **Workfront Core custom field label** (`core_label`): keeps the original text as far as possible; only emoji, non-ASCII and reporting-problematic characters are removed. Maximum length 255, PROVISIONAL (Adobe's custom form page states no length limit for labels; 255 is a placeholder).

Names are unique within a table only. Adobe states that a Core custom field API name must be unique across the whole Workfront instance; that cross-table check is a separate step and is not part of this validator.

## Order the rules run in

0. Before any field is reviewed, every in-scope table name must be approved (an unchanged name is approved in one click).
1. Table names are cleaned and a table rename map is built.
2. Old table names inside field names are replaced (resolved through the schema).
3. Characters are cleaned up and `1Q` becomes `Q1` (names only).
4. Names that start with a digit are rearranged.
5. Duplicates are checked last, on the repaired names. Always on.

Steps 2 to 4 can be switched off for review. Rules marked *baseline* are always checked.

## Settings for each name

The Planning name, the Core name and the Core label can be configured separately. By default they behave the same. Each can have its own:

- step toggles (table-name swap, character cleanup, leading digits);
- maximum length (Planning cannot exceed Adobe's documented 250);
- permission for a name to start with a digit (names only);
- leading-digit preset and word (names only);
- extra punctuation that is allowed instead of removed (names) or extra characters that are removed (labels).

A setting that differs from the default is recorded with who saved it and when.

## Verdicts

- **PASS**: complies.
- **AUTO_FIXABLE**: the only problem is one or more of the characters on the auto-delete list; deleting them gives a clean name. The fix is shown next to the original and a batch of these can be approved in one action.
- **NEEDS_HUMAN**: everything else. A proposed repair is shown where one exists, with the reason, and is approved one item at a time. Nothing is ever applied without approval.

## Rules

### EMPTY - Name is empty

A name must contain at least one character.

- Applies to: planning_name, core_name, core_label
- Pipeline step: baseline (baseline, always checked)
- Example: `(blank)` becomes `Budget`

### NON_ASCII - Non-ASCII character

Emoji, accented letters, smart quotes, zero-width characters and other non-ASCII characters are not allowed, because data exported to CSV without the right encoding corrupts them. Proposed repair (needs approval): accents become plain letters; an emoji is removed, or replaced with its Unicode name (for example Hot Beverage), as chosen for that name; other characters are removed.

- Applies to: planning_name, core_name, core_label
- Pipeline step: char_cleanup (baseline, always checked)
- Example: `Café ✨ Budget` becomes `Cafe Budget`

### WHITESPACE_CHAR - Tab, new line or no-break space

A no-break space (ASCII 160), tab, new line, vertical tab, form feed or carriage return must never appear in a name or label. Each is turned into a plain space (ASCII 32), and the spaces are then closed up. AUTO_FIXABLE.

- Applies to: planning_name, core_name, core_label
- Pipeline step: char_cleanup (baseline, always checked)
- Example: `Total\tCost` becomes `Total Cost`

### SPECIAL_CHAR - Special character

Names allow only letters, digits, spaces and hyphens. The characters ' " ` ~ ! @ # $ % ^ & * ( ) [ ] { } ; : < > , . / ? are AUTO_FIXABLE: they are deleted with nothing in their place (and any double space that leaves is closed up). Any other symbol, such as an underscore, a backslash, | + =, needs approval; the proposal turns an underscore or backslash into a space and deletes the rest. Labels keep the original text and remove only the characters Adobe documents as unsupported in labels: [ ] ( ) { } : and tab, newline, carriage return, form feed.

- Applies to: planning_name, core_name, core_label
- Pipeline step: char_cleanup (baseline, always checked)
- Example: `PO Number (from PO#s)` becomes `PO Number from POs`

### EDGE_SPACE - Leading or trailing space

No space at the start or end. Removing them is AUTO_FIXABLE.

- Applies to: planning_name, core_name, core_label
- Pipeline step: char_cleanup (baseline, always checked)
- Example: ` Budget ` becomes `Budget`

### DOUBLE_SPACE - Double space

No two spaces in a row. Closing them up is AUTO_FIXABLE.

- Applies to: planning_name, core_name, core_label
- Pipeline step: char_cleanup (baseline, always checked)
- Example: `Total  Cost` becomes `Total Cost`

### LEADING_DIGIT - Starts with a digit

A name must not start with a digit. The proposed repair (needs approval) follows the chosen preset: move the leading number token behind the descriptive words, or put a chosen word in front. A name that is only a number needs a person.

- Applies to: planning_name, core_name
- Pipeline step: leading_digits (baseline, always checked)
- Example: `500K Acme Fund` becomes `Acme Fund 500K`

### TOO_LONG - Name too long

Maximum length per target (Planning 250, confirmed; Core name and label 255, provisional). There is no automatic shortening.

- Applies to: planning_name, core_name, core_label
- Pipeline step: baseline (baseline, always checked)
- Example: `(260 characters)` becomes `(a shorter name chosen by a person)`

### QUARTER_STYLE - Quarter written 1Q

Names write quarters as Q1 to Q4, never 1Q to 4Q. Only a whole token is matched, so '1Quality' is untouched. Labels may keep the client's own convention; each label's convention is recorded instead.

- Applies to: planning_name, core_name
- Pipeline step: char_cleanup
- Example: `1Q Budget` becomes `Q1 Budget`

### RESERVED - Reserved name

Names Adobe reserves cannot be used (list to be confirmed; empty for now).

- Applies to: planning_name, core_name, core_label
- Pipeline step: baseline (baseline, always checked)
- Example: `(reserved word)` becomes `(any other name)`

### TABLE_REF - Old table name inside a field name

When a field name contains the name of a table that is being renamed (typical for lookup and rollup fields), the table's new name is suggested. The table is identified through the schema (link field -> linked table ID), never by guessing, and the old name must match as a whole name. Because every table name must be approved before fields are reviewed, a swap that follows the schema is AUTO_FIXABLE; a mention with no schema path is NEEDS_HUMAN.

- Applies to: planning_name, core_name, core_label
- Pipeline step: table_swap
- Example: `PO Number (from PO#s)  [table renamed POs]` becomes `PO Number (from POs)`

### DUPLICATE - Duplicate name

Names must be unique within a table, ignoring case. The check runs last, on the repaired names, so collisions created by the cleanup are caught. Duplicates are always a decision for a person; abandoning an empty member shrinks the group.

- Applies to: planning_name, core_name
- Pipeline step: duplicates (baseline, always checked)
- Example: `Budget / budget` becomes `Budget / Budget Q1`

## Labels and quarter convention

A label keeps the client's own convention, including `1Q` style. The validator records which convention each label uses (`Q1`, `1Q`, `mixed` or `none`) so inconsistencies can be reviewed.
