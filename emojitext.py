#!/usr/bin/env python3
"""Emoji handling shared by names, labels and descriptions. Standard library only. Pure and deterministic.

Two modes: "remove" (the default: each emoji becomes a space, which the caller's cleanup closes up) and "word" (each emoji becomes its official
Unicode name in title case, so a coffee cup becomes "Hot Beverage"). The invisible parts of an emoji (joiners, skin-tone modifiers, variation
selectors, flag letters, keycap marks) are always dropped. Only emoji are touched: other symbols such as the euro sign are left to the caller.
The names come from Python's unicodedata, so they follow the Unicode version of the Python in use.
"""
import unicodedata

EMOJI_MODES = ("remove", "word")

_RANGES = ((0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x2B05, 0x2B07), (0x2B1B, 0x2B1C), (0x2B50, 0x2B50), (0x2B55, 0x2B55), (0x231A, 0x231B),
           (0x23E9, 0x23F3), (0x23F8, 0x23FA), (0x25AA, 0x25AB), (0x25B6, 0x25B6), (0x25C0, 0x25C0), (0x25FB, 0x25FE), (0x2934, 0x2935),
           (0x3030, 0x3030), (0x303D, 0x303D), (0x3297, 0x3297), (0x3299, 0x3299))
# Always dropped, never given a word: variation selectors, the zero-width joiner, the keycap mark, skin-tone modifiers, regional-indicator (flag) letters.
_INVISIBLE = {0xFE0E, 0xFE0F, 0x200D, 0x20E3} | set(range(0x1F3FB, 0x1F400)) | set(range(0x1F1E6, 0x1F200))


def is_emoji(c):
    o = ord(c)
    return o in _INVISIBLE or any(a <= o <= b for a, b in _RANGES)


def has_emoji(s):
    return any(is_emoji(c) for c in str(s))


def replace_emoji(s, mode="remove"):
    """Replace every emoji in s. 'remove' puts a space where each run was; 'word' puts the emoji's Unicode name in title case, with spaces around it.
    Text without an emoji is returned unchanged, so a caller's own spacing is never touched."""
    s = str(s)
    if not has_emoji(s):
        return s
    if mode not in EMOJI_MODES:
        raise ValueError("unknown emoji mode")
    out = []
    for c in s:
        if not is_emoji(c):
            out.append(c)
        elif ord(c) in _INVISIBLE:
            out.append("")
        elif mode == "word":
            name = unicodedata.name(c, "")
            out.append(" " + name.title() + " " if name else " ")
        else:
            out.append(" ")
    return "".join(out)
