"""Tests for emoji handling (emojitext.py) and the remove / replace-with-word option in names, labels, tables and descriptions. Fabricated names only."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import emojitext as em, fielddesc as fd, fieldnames as fn
from fieldnames import Settings, PASS, AUTO_FIXABLE, NEEDS_HUMAN
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ok = True
def check(n, c, x=""):
    global ok; ok &= bool(c); print(("PASS " if c else "FAIL ") + n, "" if c else x)
U = chr
COFFEE, ROCKET, DRESS, HEART, THUMB, TONE, ZWJ, VS16, KEYCAP, FLAG_U, FLAG_S = U(0x2615), U(0x1F680), U(0x1F457), U(0x2764), U(0x1F44D), U(0x1F3FD), U(0x200D), U(0xFE0F), U(0x20E3), U(0x1F1FA), U(0x1F1F8)
norm = lambda s: " ".join(s.split())

# ---- the pure function
check("detects emoji and nothing else", all(em.is_emoji(c) for c in (COFFEE, ROCKET, DRESS, HEART, THUMB)) and not any(em.is_emoji(c) for c in ("A", "7", " ", "-", U(0xE9), U(0x20AC), U(0x4E2D))))
check("text without an emoji is returned untouched, spacing included", em.replace_emoji("  a  b ", "word") == "  a  b " and em.replace_emoji("Price " + U(0x20AC), "remove") == "Price " + U(0x20AC))
check("remove: an emoji becomes a space, so words are not glued together", norm(em.replace_emoji("Hello" + ROCKET + "World", "remove")) == "Hello World")
check("word: an emoji becomes its Unicode name in title case", norm(em.replace_emoji("Cafe " + COFFEE, "word")) == "Cafe Hot Beverage" and norm(em.replace_emoji(ROCKET, "word")) == "Rocket" and norm(em.replace_emoji(DRESS + " Style", "word")) == "Dress Style")
check("invisible parts (variation selector, keycap, skin tone, flag letters) are dropped in both modes", norm(em.replace_emoji("A" + VS16 + "B", "word")) == "AB"
      and norm(em.replace_emoji("1" + VS16 + KEYCAP + " One", "remove")) == "1 One" and norm(em.replace_emoji("Thumbs " + THUMB + TONE + " up", "word")) == "Thumbs Thumbs Up Sign up"
      and norm(em.replace_emoji("Flag " + FLAG_U + FLAG_S + " x", "word")) == "Flag x")
check("a joined sequence spells out each part", norm(em.replace_emoji(U(0x1F468) + ZWJ + U(0x1F469) + " Family", "word")) == "Man Woman Family")
check("a bad mode is refused only when there is something to replace", em.replace_emoji("plain", "nonsense") == "plain")
try:
    em.replace_emoji(ROCKET, "nonsense"); bad = False
except ValueError:
    bad = True
check("an unknown mode raises", bad)
check("deterministic", em.replace_emoji("A" + COFFEE + "B", "word") == em.replace_emoji("A" + COFFEE + "B", "word"))

# ---- names: the proposed repair removes by default, and can spell the emoji out
def ev(name, target="planning_name", s=None):
    return fn.evaluate_name(name, target, s or Settings(), {"field_id": "f1", "table_id": "t1"}, {}, {}, [])
r = ev("Cafe " + COFFEE)
check("default: the emoji is removed from the proposed repair, which still needs approval", r["proposed"] == "Cafe" and r["verdict"] == NEEDS_HUMAN and "NON_ASCII" in r["rules"], r)
r = ev("Cafe " + COFFEE, s=Settings(emoji_mode="word"))
check("word mode: the proposal spells the emoji out", r["proposed"] == "Cafe Hot Beverage" and r["verdict"] == NEEDS_HUMAN, r)
check("an emoji between words is separated either way", ev("Hello" + ROCKET + "World")["proposed"] == "Hello World" and ev("Hello" + ROCKET + "World", s=Settings(emoji_mode="word"))["proposed"] == "Hello Rocket World")
check("a name that is only an emoji becomes empty and needs a person; in word mode it becomes its word", ev(ROCKET)["verdict"] == NEEDS_HUMAN and ev(ROCKET)["final"] == ""
      and ev(ROCKET, s=Settings(emoji_mode="word"))["proposed"] == "Rocket")
check("labels follow the option too", ev("Budget " + ROCKET, "core_label")["proposed"] == "Budget" and ev("Budget " + ROCKET, "core_label", Settings(emoji_mode="word"))["proposed"] == "Budget Rocket")
check("the word is plain ASCII and passes the rules", fn.violations(ev("Cafe " + COFFEE, s=Settings(emoji_mode="word"))["proposed"], "planning_name", Settings()) == [])
check("each name can choose separately", ev("Cafe " + COFFEE, "planning_name", Settings(targets={"planning_name": {"emoji_mode": "word"}}))["proposed"] == "Cafe Hot Beverage"
      and ev("Cafe " + COFFEE, "core_name", Settings(targets={"planning_name": {"emoji_mode": "word"}}))["proposed"] == "Cafe")
check("table names follow the Planning name's choice", fn.clean_table_name("Shop " + DRESS, Settings(emoji_mode="word")) == "Shop Dress" and fn.clean_table_name("Shop " + DRESS, Settings()) == "Shop")
check("an emoji does not count as a lost letter (no spelling warning)", not any("check the spelling" in x for x in ev("Cafe " + COFFEE)["reasons"]))

# ---- settings
def raises_value(fn_, *a, **k):
    try:
        fn_(*a, **k)
    except ValueError as e:
        return str(e)
    return None
check("settings accept only the two modes", raises_value(Settings, emoji_mode="keep") is not None and raises_value(Settings, description_emoji_mode="nope") is not None
      and raises_value(Settings, targets={"core_name": {"emoji_mode": "keep"}}) is not None)
S2 = Settings(emoji_mode="word", description_emoji_mode="word", targets={"core_label": {"emoji_mode": "remove"}})
check("settings round-trip through their dictionary form", Settings(**S2.as_dict()).opt("planning_name", "emoji_mode") == "word" and Settings(**S2.as_dict()).opt("core_label", "emoji_mode") == "remove"
      and Settings(**S2.as_dict()).description_emoji_mode == "word")
check("the default is remove everywhere", Settings().opt("core_name", "emoji_mode") == "remove" and Settings().description_emoji_mode == "remove")

# ---- descriptions: emoji in the origin names are removed by default
f1 = {"field_id": "a", "table_name": "Shop " + DRESS, "field_name": "Cafe " + COFFEE, "data_type": "singleLineText", "field_description": "Hot " + COFFEE + " drink"}
by = {"a": f1}
check("descriptions remove an emoji from the origin names by default", fd.generate_description(f1, by) == "Source: Airtable. Origins: Shop.Cafe.")
check("or spell it out", fd.generate_description(f1, by, emoji_mode="word") == "Source: Airtable. Origins: Shop Dress.Cafe Hot Beverage.")
check("the same applies to Airtable's own description when it is included", fd.generate_description(f1, by, True) == "Source: Airtable. Origins: Shop.Cafe. Airtable description: Hot drink"
      and fd.generate_description(f1, by, True, "word").endswith("Airtable description: Hot Hot Beverage drink"))
check("a generated description with an emoji removed has no non-ASCII warning", fd.validate_description(fd.generate_description(f1, by)) == [])
check("names without an emoji are untouched (original spacing kept for traceability)", fd.generate_description({"field_id": "b", "table_name": "T", "field_name": "A  B", "data_type": "x"}, {}) == "Source: Airtable. Origins: T.A  B.")
check("a description with an emoji still round-trips through the parser after the option is applied", fd.parse_lineage(fd.generate_description(f1, by, emoji_mode="word")) is not None)
sys.exit(0 if ok else 1)
