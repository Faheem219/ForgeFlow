#!/usr/bin/env python3
"""Word-for-word fidelity check of the Springer manuscript against the draft.

The marked master records every deliberate wording change in \\chg{...} /
\\fix{...} (added/replaced text) or \\del{...} / \\fixdel{...} (removed draft
text). If we take the master, drop every addition and unwrap every removal, we should get
back the 4 Oct 2026 draft exactly -- apart from pure formatting (LaTeX
markup, citation and cross-reference syntax, the reference list).

This script does that reconstruction, reduces both sides to a stream of
lower-case word tokens, and reports every place they differ. Any reported
difference is an *unmarked* change: either wrap it in markup or restore
the draft wording.

Cross-references are resolved through the .aux file (compile first), and the
draft's IEEE-style numbers (Table VII, Section IX-A) are converted to the
arabic form sn-jnl prints (Table 7, Section 9.1) before comparison.

Usage: python3 tools/fidelity_check.py [marked.tex]
Exit status 0 = no unmarked differences.
"""

from __future__ import annotations

import difflib
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DRAFT_DOCX = HERE.parent / "IEEE Conference Paper Draft - Revised 4 Oct 2026.docx"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_clean import matching_brace, pick_rw, resolve, strip_markup_block  # noqa: E402

ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}


def drop_chg_keep_del(text: str) -> str:
    """Reconstruct the draft wording: remove \\chg{..}, unwrap \\del{..}."""
    # Swap the macro names so resolve() (which unwraps chg, drops del)
    # does the inverse job.
    # The authors' 16 Oct rewrites (\\rw{new}{old}) are checked separately by
    # rewrite_check.py; here every rewritten passage contributes its old text,
    # so this check still proves nothing outside those passages changed.
    text = pick_rw(text, 1)
    inverse = {"chg": "del", "fix": "fixdel", "del": "chg", "fixdel": "fix"}
    swapped = re.sub(r"\\(chg|fixdel|fix|del)\{", lambda m: "\\" + inverse[m.group(1)] + "{", text)
    return resolve(swapped)


def unwrap_command(text: str, name: str, replacement: str = "{body}\n\n") -> str:
    out, pos = [], 0
    pat = re.compile(r"\\" + name + r"\*?(?:\[[^\]]*\])?\{")
    while (m := pat.search(text, pos)) is not None:
        close = matching_brace(text, m.end() - 1)
        out.append(text[pos : m.start()])
        out.append(replacement.format(body=text[m.end() : close]))
        pos = close + 1
    out.append(text[pos:])
    return "".join(out)


def labels_from_aux(tex: Path) -> dict[str, str]:
    aux = tex.with_suffix(".aux")
    if not aux.exists():
        sys.exit(f"{aux.name} not found -- compile the manuscript first")
    labels = {}
    for m in re.finditer(r"\\newlabel\{([^}]*)\}\{\{([^}]*)\}", aux.read_text(errors="replace")):
        labels[m.group(1)] = m.group(2)
    return labels


def tex_to_text(tex_src: str, labels: dict[str, str]) -> str:
    body = tex_src.split("\\begin{document}", 1)[-1]
    body = body.split("\\bibliography{", 1)[0]
    body = re.sub(r"(?<!\\)%.*", "", body)  # comments
    # [Content Author, 15 Oct] Images carry no wording; pandoc would print each as "[image]".
    body = re.sub(r"\\includegraphics(?:\[[^\]]*\])?\{[^}]*\}", " ", body)
    body = re.sub(r"\\(?:ref|autoref)\{([^}]*)\}", lambda m: labels.get(m.group(1), "??"), body)
    body = re.sub(r"\\cite[pt]?\*?(?:\[[^\]]*\])?\{[^}]*\}", " ", body)
    # Front matter macros whose argument is prose we must keep.
    for name in ("abstract", "keywords", "title", "caption", "bmhead"):
        body = unwrap_command(body, name)
    # Author block is reformatted wholesale for sn-jnl; compared separately.
    body = re.sub(r"\\(?:author|affil|email|fnm|sur|orgdiv|orgname|orgaddress|street|city|postcode|state|country|equalcont)\*?(?:\[[^\]]*\])?\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", " ", body)
    tmp = HERE / "review" / ".fidelity-body.tex"
    tmp.write_text("\\documentclass{article}\\begin{document}\n" + body + "\n\\end{document}\n")
    plain = subprocess.run(
        ["pandoc", "-f", "latex", "-t", "plain", "--wrap=none", "--columns=100000", str(tmp)],
        capture_output=True, text=True, check=True,
    ).stdout
    tmp.unlink()
    # [Content Author, 15 Oct] enumerate labels ("1. ") are layout, as on the draft side.
    plain = re.sub(r"(?m)^\s*\d+\.\s+", "", plain)
    return plain


def draft_text() -> str:
    plain = subprocess.run(
        ["pandoc", str(DRAFT_DOCX), "-t", "plain", "--wrap=none", "--columns=100000"],
        capture_output=True, text=True, check=True,
    ).stdout
    # Drop the author block (everything before the abstract) and references.
    # [Content Author, 15 Oct] but keep the title (first line), which the .tex side keeps too.
    title = plain.split("\n", 1)[0]
    plain = plain[plain.find("Abstract") :]
    # [Content Author, 15 Oct] docx images render as "[alt text]" lines ("[]", and
    # "[Function-level repair results]" for Fig. 7); alt text is not printed wording.
    plain = re.sub(r"(?m)^\[[^\]\n]*\]\s*$", "", plain)
    plain = re.split(r"\n\s*References\s*\n", plain)[0]
    # IEEE heading/caption/list numbering ("III. ", "7. ", "A. ", "2) ") and
    # the "Abstract--" / "Keywords--" run-in labels are layout, not wording.
    plain = re.sub(r"(?m)^\s*(?:[IVX]+|\d+|[A-Z])\.\s+", "", plain)
    plain = re.sub(r"(?m)^\s*\d+\)\s+", "", plain)
    plain = re.sub(r"^Abstract\s*\u2014+", "", plain)
    plain = re.sub(r"(?m)^Keywords\s*\u2014+", "", plain)
    # IEEE citation brackets -> nothing (sn-jnl renders \cite itself).
    plain = re.sub(r"\[\d+(?:\s*[,\u2013-]\s*\d+)*\]", " ", plain)

    def roman_ref(m: re.Match[str]) -> str:
        num = ROMAN.get(m.group(2))
        if num is None:
            return m.group(0)
        sub = f".{ord(m.group(3)) - 64}" if m.group(3) else ""
        return f"{m.group(1)} {num}{sub}"

    plain = re.sub(r"\b(Table|Tables|Section|Sections)\s+([IVX]+)\b(?:-([A-Z])\b)?", roman_ref, plain)
    return title + "\n\n" + plain


def tokens(text: str) -> list[str]:
    text = text.replace("\u00a0", " ").replace("\u202f", " ")
    return re.findall(r"[a-z0-9]+", text.lower())


def main() -> int:
    tex = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "forgeflow-springer-marked.tex"
    src = strip_markup_block(tex.read_text(encoding="utf-8"))
    reconstructed = tex_to_text(drop_chg_keep_del(src), labels_from_aux(tex))
    a, b = tokens(draft_text()), tokens(reconstructed)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    ops = [op for op in sm.get_opcodes() if op[0] != "equal"]

    deleted = {" ".join(a[i1:i2]) for tag, i1, i2, _, _ in ops if tag in ("delete", "replace")}
    issues = 0
    print(f"draft tokens: {len(a)}  reconstructed tokens: {len(b)}  similarity: {sm.ratio():.4f}\n")
    for tag, i1, i2, j1, j2 in ops:
        old, new = " ".join(a[i1:i2]), " ".join(b[j1:j2])
        if tag == "insert" and new in deleted:
            continue  # the other half of a moved block, reported below
        if tag == "delete" and old in {" ".join(b[x1:x2]) for t, _, _, x1, x2 in ops if t == "insert"}:
            print(f"MOVED   (block relocated, e.g. a caption): {old[:100]}")
            continue
        issues += 1
        ctx = " ".join(a[max(0, i1 - 6) : i1])
        print(f"{tag.upper():7} ...{ctx} [[draft: {old[:160]}]] -> [[tex: {new[:160]}]]")
    print(f"\n{issues} unmarked difference(s)")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
