-- Migration 015 (PROPOSED, not applied): the confirmed direction of each link field. Adds SIX columns to ONE table: field_relationships.
-- Additive only: nothing is renamed or dropped, existing rows keep their values (the new columns are NULL, or 0 for the three flags).
-- ADD COLUMN cannot be repeated: apply_migrations.py must check PRAGMA table_info first (it is not wired in until you confirm this is applied).
--
-- Why: the tool detects pairs and SUGGESTS a direction; a person CONFIRMS each pair; the confirmed directions then set the load order. The
-- existing columns cannot hold that: cardinality is one side's own setting, and is_primary is one 0/1 answer with no room for 'one to one'
-- or 'many to many' or for who decides which table loads first. Detect, do not decide: suggested_type is what the tool proposed from the
-- schema; relationship_type is what a person chose and stays NULL until they do. Who and when reuse approved_by and approved_at.
--
-- The choice is made on ONE field and sets its partner (shown as auto-set and changeable from either end, which updates the other):
--   one_to_many   this field's table loads first        partner = many_to_one
--   many_to_one   the other table loads first           partner = one_to_many
--   one_to_one    set by the tiebreaker                 partner = one_to_one
--   many_to_many  set by the tiebreaker                 partner = many_to_many
ALTER TABLE field_relationships ADD COLUMN suggested_type TEXT CHECK(suggested_type IN ('one_to_many', 'many_to_one', 'one_to_one', 'many_to_many'));
ALTER TABLE field_relationships ADD COLUMN relationship_type TEXT CHECK(relationship_type IN ('one_to_many', 'many_to_one', 'one_to_one', 'many_to_many'));
-- Only meaningful for one_to_one and many_to_many: which table loads first. 'automatic' = the load order decides (a derived, non-binding edge).
ALTER TABLE field_relationships ADD COLUMN tiebreaker TEXT CHECK(tiebreaker IN ('this_first', 'other_first', 'automatic'));
-- 1 = a person confirmed the direction (approved_by and approved_at say who and when). 0 = still only suggested.
ALTER TABLE field_relationships ADD COLUMN confirmed INTEGER NOT NULL DEFAULT 0 CHECK(confirmed IN (0, 1));
-- 1 = an automatic pair that the confirmed order already settles, so the tool marked it resolved (not a person's choice). 0 = not derived.
ALTER TABLE field_relationships ADD COLUMN derived INTEGER NOT NULL DEFAULT 0 CHECK(derived IN (0, 1));
-- 1 = this row was set because its partner was chosen (so the screen can label it 'auto-set').
ALTER TABLE field_relationships ADD COLUMN auto_set INTEGER NOT NULL DEFAULT 0 CHECK(auto_set IN (0, 1));
