"""Column padding that counts what a terminal draws, not what Python counts.

Every table in this report mixes Chinese with ASCII, and ``f"{text:<30}"`` pads
by code point: a CJK character occupies two cells and is counted as one, so a
column of mixed labels comes out ragged exactly where it matters — the
capability matrix, which a person reads down rather than across.

``unicodedata.east_asian_width`` gives the answer in the two classes that
matter here (``W`` wide, ``F`` fullwidth), and combining marks take no cell of
their own.
"""

from __future__ import annotations

import unicodedata

__all__ = ["display_width", "pad"]


def display_width(text: str) -> int:
    width = 0
    for character in text:
        if unicodedata.combining(character):
            continue
        width += 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
    return width


def pad(text: str, width: int) -> str:
    """Left-align ``text`` in a field ``width`` terminal cells wide."""
    return text + " " * max(0, width - display_width(text))
