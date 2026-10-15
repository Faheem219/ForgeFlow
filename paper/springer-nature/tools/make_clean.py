#!/usr/bin/env python3
"""Produce the clean submission .tex from the change-marked master.

The marked master (forgeflow-springer-marked.tex) wraps every wording change
made to the 4 Oct 2026 draft in one of four macros:

    \\chg{new text}      text added or replaced to fit the Springer format
    \\del{old text}      draft text removed to fit the Springer format
    \\fix{new text}      factual correction against the implementation
    \\fixdel{old text}   draft text removed as a factual correction

and defines them between the lines ``%%% CHANGE-MARKUP-BEGIN`` and
``%%% CHANGE-MARKUP-END``. The clean file keeps the content of \\chg and
\\fix, drops \\del and \\fixdel entirely, and removes the markup block, so
Springer receives plain sn-jnl LaTeX with no custom macros and no coloured
text.

Usage: python3 tools/make_clean.py [marked.tex] [clean.tex]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REMOVE = ("del", "fixdel")
MARKUP = re.compile(r"\\(chg|fixdel|fix|del)\{")


def matching_brace(text: str, open_idx: int) -> int:
    """Return the index of the brace closing the one at ``open_idx``."""
    depth = 0
    i = open_idx
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2  # skip an escaped char such as \{ or \}
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise SystemExit(f"unbalanced brace opened at offset {open_idx}")


def resolve(text: str, keep_del: bool = False) -> str:
    """Unwrap every addition; drop (or, with keep_del, unwrap) every removal.

    A removal that is the only thing on its line(s) takes the line with it, so
    removing it never leaves a blank line that would split a paragraph.
    """
    out: list[str] = []
    pos = 0
    while (m := MARKUP.search(text, pos)) is not None:
        close = matching_brace(text, m.end() - 1)
        inner = resolve(text[m.end() : close], keep_del)
        start, end = m.start(), close + 1
        if m.group(1) in REMOVE and not keep_del:
            line_start = text.rfind("\n", 0, start) + 1
            line_end = text.find("\n", end)
            line_end = len(text) if line_end == -1 else line_end
            alone = not text[line_start:start].strip() and not text[end:line_end].strip()
            if alone and line_start >= pos:
                out.append(text[pos:line_start])
                pos = min(line_end + 1, len(text))
                continue
            inner = ""
        out.append(text[pos:start])
        out.append(inner)
        pos = end
    out.append(text[pos:])
    return "".join(out)


def strip_markup_block(text: str) -> str:
    return re.sub(
        r"^%%% CHANGE-MARKUP-BEGIN.*?^%%% CHANGE-MARKUP-END[^\n]*\n",
        "",
        text,
        flags=re.S | re.M,
    )


def make_clean(marked: str) -> str:
    clean = resolve(strip_markup_block(marked))
    # Removing an inline \del can leave a double space; tidy it.
    clean = re.sub(r"(?<=\S)  +(?=\S)", " ", clean)
    if re.search(r"\\(?:chg|del|fix|fixdel)\b", clean):
        raise SystemExit("change markup left in the clean file")
    return clean


def main() -> None:
    here = Path(__file__).resolve().parent.parent
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else here / "forgeflow-springer-marked.tex"
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else here / "forgeflow-springer.tex"
    clean = make_clean(src.read_text(encoding="utf-8"))
    dst.write_text(clean, encoding="utf-8")
    print(f"wrote {dst.name} ({len(clean.splitlines())} lines)")


if __name__ == "__main__":
    main()
