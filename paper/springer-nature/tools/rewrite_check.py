#!/usr/bin/env python3
"""Check that the authors' 16 Oct rewrites are reproduced word for word.

After the Turnitin AI-writing report, the authors rewrote the flagged
passages (review/authors-rewrites-2026-10-16.docx). In the master each
rewritten passage is stored as \\rw{new}{old}; inside ``new``, \\fix{...} adds
and \\fixdel{...} removes words to correct factual errors and broken sentences
in the rewrite.

This script takes every ``new`` argument in order, undoes the corrections
(drops \\fix, unwraps \\fixdel) to recover the authors' text, and compares its
word tokens with the .docx. Any difference is an unmarked edit to the
authors' wording. It then lists every correction, so each can be reviewed.

Usage: python3 tools/rewrite_check.py   (compile first: refs resolve via .aux)
Exit status 0 = the authors' wording is reproduced exactly.
"""

from __future__ import annotations

import difflib
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fidelity_check import HERE, labels_from_aux, tex_to_text, tokens  # noqa: E402
from make_clean import matching_brace, resolve, strip_markup_block  # noqa: E402

DOCX = HERE / "review" / "authors-rewrites-2026-10-16.docx"
MASTER = HERE / "forgeflow-springer-marked.tex"


def rw_new_args(text: str) -> list[str]:
    out, pos = [], 0
    while (m := re.compile(r"\\rw\{").search(text, pos)) is not None:
        close = matching_brace(text, m.end() - 1)
        out.append(text[m.end() : close])
        pos = close + 1
    return out


def authors_version(new: str) -> str:
    """Undo the corrections: drop \\fix additions, restore \\fixdel removals."""
    inverse = {"fix": "fixdel", "fixdel": "fix", "chg": "del", "del": "chg"}
    swapped = re.sub(r"\\(chg|fixdel|fix|del)\{", lambda m: "\\" + inverse[m.group(1)] + "{", new)
    return resolve(swapped)


def corrections(new: str) -> list[tuple[str, str]]:
    found = []
    for m in re.finditer(r"\\(fixdel|fix)\{", new):
        close = matching_brace(new, m.end() - 1)
        found.append(("removed" if m.group(1) == "fixdel" else "added", new[m.end() : close]))
    return found


def main() -> int:
    src = strip_markup_block(MASTER.read_text(encoding="utf-8"))
    blocks = rw_new_args(src)
    labels = labels_from_aux(MASTER)
    body = "\\begin{document}\n" + "\n\n".join(authors_version(b) for b in blocks) + "\n\\end{document}"
    ours = tokens(tex_to_text(body, labels))

    docx = subprocess.run(
        ["pandoc", str(DOCX), "-t", "plain", "--wrap=none", "--columns=100000"],
        capture_output=True, text=True, check=True,
    ).stdout
    docx = re.sub(r"\[\d+(?:\s*[,\u2013-]\s*\d+)*\]", " ", docx)  # [13], [12, 14]
    theirs = tokens(docx)

    sm = difflib.SequenceMatcher(a=theirs, b=ours, autojunk=False)
    issues = 0
    print(f"rewritten passages: {len(blocks)}  docx tokens: {len(theirs)}  "
          f"reconstructed tokens: {len(ours)}  similarity: {sm.ratio():.4f}\n")
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        issues += 1
        ctx = " ".join(theirs[max(0, i1 - 6) : i1])
        print(f"{tag.upper():7} ...{ctx} [[docx: {' '.join(theirs[i1:i2])[:160]}]] "
              f"-> [[tex: {' '.join(ours[j1:j2])[:160]}]]")
    print(f"\n{issues} unmarked difference(s) from the authors' wording\n")

    if "--list" in sys.argv:
        for n, b in enumerate(blocks, 1):
            for kind, txt in corrections(b):
                print(f"  block {n:2}: {kind:7} {txt}")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
