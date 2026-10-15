#!/usr/bin/env python3
"""Automated checks for GUIDELINES_CHECKLIST.md (Springer Nature / sn-jnl).

Runs every check in the checklist that can be decided mechanically and prints
one line per checklist ID: PASS, FAIL, WARN (needs a human look) or INFO.
Checks that need the build outputs (.log, .bbl, .pdf) are skipped with a
note if those files do not exist yet -- run tools/build.sh first.

Usage (from the repository root or anywhere):
    python3 paper/springer-nature/review/guideline_checks.py [--dir paper/springer-nature]

Exit status: 0 if no FAIL, 1 otherwise. WARN lines still need to be read.
"""

from __future__ import annotations

import argparse
import filecmp
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # paper/springer-nature
TEMPLATE = HERE.parent / "sn-article-template"

TEMPLATE_PKGS = {
    "graphicx", "multirow", "amsmath", "amssymb", "amsfonts", "amsthm", "mathrsfs",
    "appendix", "xcolor", "textcomp", "manyfoot", "booktabs", "algorithm",
    "algorithmicx", "algpseudocode", "listings",
}
FORBIDDEN_PKGS = {
    "fontspec", "fontenc", "lmodern", "times", "mathptmx", "helvet", "courier",
    "newtxtext", "newtxmath", "palatino", "mathpazo", "kpfonts", "libertine",
    "geometry", "hyperref", "natbib", "biblatex", "cite", "subfig", "subcaption",
    "subfigure", "titlesec", "fancyhdr", "ulem", "soul", "setspace", "unicode-math",
}
DECLARATIONS = [
    ("Funding", r"funding"),
    ("Competing interests", r"competing interests|conflict of interest"),
    ("Ethics approval and consent to participate", r"ethics approval"),
    ("Consent for publication", r"consent for publication|consent to publish"),
    ("Data availability", r"data availability"),
    ("Materials availability", r"materials? availability"),
    ("Code availability", r"code availability"),
    ("Author contribution", r"author(s'|s)? contributions?"),
]
results: list[tuple[str, str, str]] = []


def report(gid: str, status: str, msg: str) -> None:
    results.append((gid, status, msg))


def strip_comments(s: str) -> str:
    return re.sub(r"(?<!\\)%.*", "", s)


def matching_brace(text: str, open_idx: int) -> int:
    depth, i = 0, open_idx
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(text) - 1


def arg_of(src: str, macro: str) -> str | None:
    m = re.search(r"\\" + macro + r"\*?(?:\[[^\]]*\])?\{", src)
    if not m:
        return None
    return src[m.end() : matching_brace(src, m.end() - 1)]


def non_ascii(path: Path) -> list[str]:
    hits = []
    for n, line in enumerate(path.read_bytes().split(b"\n"), 1):
        if any(b > 127 for b in line):
            hits.append(f"{path.name}:{n}: {line.decode('utf-8', 'replace')[:90]}")
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(HERE))
    ap.add_argument("--clean", default="forgeflow-springer.tex")
    ap.add_argument("--marked", default="forgeflow-springer-marked.tex")
    a = ap.parse_args()
    d = Path(a.dir)
    clean_p, marked_p = d / a.clean, d / a.marked
    if not clean_p.exists():
        print(f"{clean_p} not found -- run tools/build.sh (it creates the clean file) first")
        return 1
    clean = clean_p.read_text(encoding="utf-8", errors="replace")
    marked = marked_p.read_text(encoding="utf-8", errors="replace") if marked_p.exists() else ""
    pre, _, body = clean.partition("\\begin{document}")
    pre_nc, body_nc = strip_comments(pre), strip_comments(body)
    stem = clean_p.with_suffix("")

    # G01 class + bst identical to the Dec 2024 v3.1 template package
    same_cls = filecmp.cmp(d / "sn-jnl.cls", TEMPLATE / "sn-jnl.cls", shallow=False)
    same_bst = filecmp.cmp(d / "sn-mathphys-num.bst", TEMPLATE / "bst" / "sn-mathphys-num.bst", shallow=False)
    report("G01", "PASS" if same_cls and same_bst else "FAIL",
           f"sn-jnl.cls identical={same_cls}, sn-mathphys-num.bst identical={same_bst}")

    # G02/G03 document class line
    dc = re.findall(r"\\documentclass(\[[^\]]*\])?\{([^}]*)\}", pre_nc)
    ok = len(dc) == 1 and dc[0][1] == "sn-jnl" and "pdflatex" in dc[0][0] and "sn-mathphys-num" in dc[0][0]
    report("G02", "PASS" if ok else "FAIL", f"documentclass = {dc}")
    extra = [o for o in (dc[0][0].strip("[]").split(",") if dc else []) if o.strip() not in ("pdflatex", "sn-mathphys-num")]
    if extra:
        report("G02", "WARN", f"extra class options {extra} -- journal-dependent (referee/lineno/iicol); confirm intended")

    # G04 single file
    inc = re.findall(r"\\(input|include|subfile|import)\b", strip_comments(clean))
    report("G04", "PASS" if not inc else "FAIL", f"\\input/\\include occurrences: {inc}")

    # G06/G07 packages
    pkgs = []
    for opts, names in re.findall(r"\\usepackage(\[[^\]]*\])?\{([^}]*)\}", pre_nc):
        pkgs += [p.strip() for p in names.split(",")]
    bad = sorted(set(pkgs) & FORBIDDEN_PKGS)
    other = sorted(set(pkgs) - TEMPLATE_PKGS - FORBIDDEN_PKGS)
    report("G06/G07", "PASS" if not bad else "FAIL", f"forbidden packages: {bad or 'none'}")
    if other:
        report("G07", "WARN", f"non-template packages need a one-line justification: {other}")
    if re.search(r"\\bibliographystyle", strip_comments(clean)):
        report("G07", "FAIL", "\\bibliographystyle present -- sn-jnl already sets it (BibTeX: 'another \\bibstyle')")

    # G08 ASCII-only sources
    hits = []
    for p in [clean_p, marked_p, *sorted(d.glob("*.bib"))]:
        if p.exists():
            hits += non_ascii(p)
    report("G08", "PASS" if not hits else "FAIL", "non-ASCII bytes: " + ("none" if not hits else f"{len(hits)} lines, e.g. {hits[:3]}"))

    # G09 OT1 text-mode traps: < > | outside math
    no_math = re.sub(r"\$[^$]*\$", "", body_nc)
    no_math = re.sub(r"\\begin\{(tabular\*?|tabularx)\}(\{[^}]*\}){1,2}", "", no_math)
    traps = [m.group(0) for m in re.finditer(r".{0,25}[<>|].{0,15}", no_math)]
    report("G09", "PASS" if not traps else "WARN", f"text-mode < > | (print as wrong glyphs in OT1): {traps[:5] or 'none'}")

    # G10 manual spacing / colour in the clean file
    sp = re.findall(r"\\(vspace|vskip|hspace|bigskip|medskip|smallskip|clearpage|newpage|pagebreak|linebreak|enlargethispage)\b", body_nc)
    col = re.findall(r"\\(color|textcolor|colorbox)\b", body_nc)
    report("G10", "PASS" if not sp and not col else "WARN", f"spacing cmds {sp or 'none'}; colour cmds {col or 'none'}")

    # G11 clean file free of markup/placeholders
    left = re.findall(r"\\(?:chg|del|fix|fixdel)\{|CHANGE-MARKUP|AUTHOR INPUT|TODO|FIXME|XXX", clean)
    report("G11", "PASS" if not left else "FAIL", f"markup/placeholders in clean file: {left or 'none'}")

    # G12 title
    title = arg_of(body_nc, "title")
    short = "yes" if re.search(r"\\title\[", body_nc) else "no"
    report("G12", "PASS" if title else "FAIL", f"title present={bool(title)}; short title={short}")

    # G13/G14 authors, corresponding author, e-mails, honorifics
    authors = re.findall(r"\\author(\*?)", body_nc)
    emails = re.findall(r"\\email\{([^}]*)\}", body_nc)
    corr = authors.count("*")
    report("G13", "PASS" if len(authors) == 4 else "FAIL", f"{len(authors)} \\author entries (draft has 4)")
    hon = re.findall(r"\\author[^\n]*\b(Dr|Prof|Mr|Ms|Mrs)\.", body_nc)
    report("G13", "PASS" if not hon else "FAIL", f"honorifics in author block: {hon or 'none'}")
    report("G14", "PASS" if corr == 1 and len(emails) == 4 else "FAIL", f"corresponding authors={corr} (need 1), emails={emails}")

    # G15 affiliation
    affils = re.findall(r"\\affil\*?(?:\[[^\]]*\])?\{", body_nc)
    report("G15", "PASS" if affils and "\\country{India}" in body_nc and "\\city{Pune}" in body_nc else "FAIL", f"{len(affils)} \\affil; city/country tagged")

    # G16 abstract
    ab = arg_of(body_nc, "abstract")
    if ab is None:
        report("G16", "FAIL", "no \\abstract{}")
    else:
        words = len(re.sub(r"\\[a-zA-Z]+\*?|[{}$~]", " ", ab).split())
        cite = re.search(r"\\cite", ab)
        eqn = re.search(r"\\\[|\\begin\{(equation|align)", ab)
        report("G16", "FAIL" if cite or eqn else "INFO",
               f"abstract {words} words (journal guide 150-250; template guide <=200) cites={bool(cite)} display-eqs={bool(eqn)}")

    # G17 keywords
    kw = arg_of(body_nc, "keywords")
    n = len([k for k in (kw or "").split(",") if k.strip()])
    report("G17", "INFO" if kw else "FAIL", f"{n} keywords (many Springer journals ask 4-6)")

    # G18 maketitle
    report("G18", "PASS" if "\\maketitle" in body_nc else "FAIL", "\\maketitle present")

    # G19 Introduction without subheadings
    secs = list(re.finditer(r"\\(section|subsection|subsubsection)\*?\{([^}]*)\}", body_nc))
    intro_sub = []
    for i, m in enumerate(secs):
        if m.group(1) == "section" and m.group(2).strip().lower().startswith("introduction"):
            for m2 in secs[i + 1 :]:
                if m2.group(1) == "section":
                    break
                intro_sub.append(m2.group(2))
    report("G19", "PASS" if not intro_sub else "FAIL", f"subheadings inside Introduction: {intro_sub or 'none'}")

    # G27 figure files
    figs = re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]*)\}", body_nc)
    names = {p.name for p in d.iterdir()}
    probs = [f for f in figs if "/" in f or (f not in names and not any(f + e in names for e in (".png", ".pdf", ".jpg")))]
    report("G27", "PASS" if figs and not probs else "FAIL", f"{len(figs)} \\includegraphics; path/case problems: {probs or 'none'}")

    # G30/G34 figures and tables cited in text in consecutive order
    b = re.sub(r"\\begin\{verbatim\}.*?\\end\{verbatim\}", "", body_nc, flags=re.S)
    b = re.sub(r"\\verb(.).*?\1", "", b)
    flt = r"\\begin\{((?:sideways)?(?:figure|table))(\*?)\}(.*?)\\end\{\1\2\}"
    labels = []
    for env in re.finditer(flt, b, re.S):
        m = re.search(r"\\label\{([^}]*)\}", env.group(3))
        labels.append(("figure" if "figure" in env.group(1) else "table", m.group(1) if m else None))
    text = re.sub(flt, "", b, flags=re.S)
    first: list[str] = []
    for m in re.finditer(r"\\(?:auto)?ref\{([^}]*)\}", text):
        if m.group(1) not in first:
            first.append(m.group(1))
    for gid, kind in (("G30", "figure"), ("G34", "table")):
        order = [l for k, l in labels if k == kind]
        cited = [l for l in first if l in order]
        ok = cited == order and None not in order
        report(gid, "PASS" if ok else "FAIL", f"{kind}s in float order {order}; first in-text citations {cited}")

    # G38/G41 bibliography order and completeness (needs build)
    bbl, log, blg = stem.with_suffix(".bbl"), stem.with_suffix(".log"), stem.with_suffix(".blg")
    cites: list[str] = []
    for m in re.finditer(r"\\cite[pt]?\*?(?:\[[^\]]*\])*\{([^}]*)\}", body_nc):
        for k in m.group(1).split(","):
            k = k.strip()
            if k and k not in cites:
                cites.append(k)
    if bbl.exists():
        items = re.findall(r"\\bibitem(?:\[[^\]]*\])?\{([^}]*)\}", bbl.read_text(errors="replace"))
        report("G38", "PASS" if items == cites and len(items) == 32 else "FAIL",
               f"{len(items)} bibitems (draft has 32); .bbl order == first-citation order: {items == cites}")
    else:
        report("G38", "INFO", "no .bbl yet -- run tools/build.sh")
    if blg.exists():
        w = [l for l in blg.read_text(errors="replace").splitlines() if l.startswith("Warning--") or "error message" in l]
        report("G41", "PASS" if not w else "WARN", f"BibTeX warnings/errors: {w[:6] or 'none'}")

    # G05/G58 compile log
    if log.exists():
        lg = log.read_text(errors="replace")
        errs = re.findall(r"^! .*", lg, re.M)
        undef = re.findall(r".*(?:undefined|Undefined).*", lg)
        over = re.findall(r"Overfull \\hbox \((\d+\.\d+)pt", lg)
        big = [o for o in over if float(o) > 10]
        report("G05", "PASS" if not errs and not undef else "FAIL", f"errors={len(errs)} undefined={len(undef)} {undef[:2]}")
        report("G36", "PASS" if not big else "WARN", f"overfull hboxes={len(over)}, >10pt: {big or 'none'}")
        miss = re.findall(r"Missing character: There is no .*", lg)
        report("G08", "PASS" if not miss else "FAIL", f"'Missing character' in log: {len(miss)}")
    else:
        report("G05", "INFO", "no .log yet -- run tools/build.sh")

    # G06 fonts in the PDF, '??' in the PDF
    pdf = stem.with_suffix(".pdf")
    if pdf.exists() and shutil.which("pdffonts"):
        rows = subprocess.run(["pdffonts", str(pdf)], capture_output=True, text=True).stdout.splitlines()[2:]
        t3 = [r for r in rows if "Type 3" in r]
        # Fonts embedded inside included figure PDFs (e.g. matplotlib's) are artwork, not text fonts.
        fig_fonts = set()
        for fp in d.glob("fig*.pdf"):
            for r in subprocess.run(["pdffonts", str(fp)], capture_output=True, text=True).stdout.splitlines()[2:]:
                fig_fonts.add(re.sub(r"^[A-Z]{6}\+", "", r.split()[0]))
        # CM family: Knuth CM/AMS Type 1 and cm-super (SFRM, SFBX, ...: T1/TS1 Computer Modern).
        cm = r"^(?:[A-Z]{6}\+)?(CM|MSAM|MSBM|EUFM|EUSM|EUR|RSFS|LINE|LCIRCLE|SF[A-Z]{2}\d)"
        names = [r.split()[0] for r in rows]
        art = [n for n in names if re.sub(r"^[A-Z]{6}\+", "", n) in fig_fonts]
        odd = [n for n in names if not re.match(cm, n) and n not in art]
        report("G06", "PASS" if not t3 and not odd else "WARN",
               f"Type 3 fonts: {len(t3)}; non-CM text fonts: {odd or 'none'}; fonts from figure PDFs: {art or 'none'}")
    if pdf.exists() and shutil.which("pdftotext"):
        txt = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True).stdout
        report("G05", "PASS" if "??" not in txt else "FAIL", "'??' (unresolved ref/cite) in PDF text" + (" found" if "??" in txt else ": none"))
        report("G09", "PASS" if not re.search("[\u00a1\u00bf]", txt) else "FAIL", "inverted !/? glyphs (OT1 '<' '>' trap) in PDF")

    # G44-G52 Declarations
    back = body_nc.split("\\backmatter", 1)
    if len(back) < 2:
        report("G43", "FAIL", "no \\backmatter")
        back_txt = ""
    else:
        back_txt = back[1].split("\\bibliography", 1)[0]
        report("G43", "PASS", "\\backmatter present")
    has_decl = re.search(r"\\section\*\{(Statements and )?Declarations\}", back_txt)
    report("G44", "PASS" if has_decl else "FAIL", "\\section*{Declarations} after \\backmatter")
    for gid, (name, pat) in zip(["G45", "G46", "G47", "G48", "G49", "G50", "G51", "G52"], DECLARATIONS):
        report(gid, "PASS" if re.search(pat, back_txt, re.I) else "FAIL", f"Declarations item '{name}'")

    # G60/G61 change markup inventory (marked file)
    if marked:
        mb = strip_comments(marked.split("\\begin{document}", 1)[-1])
        marks = []
        for m in re.finditer(r"\\(chg|fixdel|fix|del)\{", mb):
            close = matching_brace(mb, m.end() - 1)
            marks.append((m.group(1), mb[m.end() : close], mb[: m.start()].count("\n") + 1))
        count = {k: sum(x == k for x, _, _ in marks) for k in ("chg", "del", "fix", "fixdel")}
        report("G60", "INFO", f"markup in marked body: {count} -- each \\chg/\\del must cite a G-ID; each \\fix/\\fixdel must cite implementation evidence")
        digits = [f"{k}@line{ln}:{t[:40]}" for k, t, ln in marks if re.search(r"\d", t) and not re.search(r"\\ref\{", t)]
        fmt = [d for d in digits if not d.startswith("fix")]
        fixes = [d for d in digits if d.startswith("fix")]
        report("G61", "PASS" if not fmt else "WARN", f"\\chg/\\del containing digits (format changes must not touch numbers): {fmt or 'none'}")
        if fixes:
            report("G61", "INFO", f"\\fix/\\fixdel containing digits (factual corrections; verify each against its evidence): {fixes}")

    width = max(len(g) for g, _, _ in results)
    for gid, status, msg in results:
        print(f"{gid:<{width}}  {status:<4}  {msg}")
    fails = sum(s == "FAIL" for _, s, _ in results)
    warns = sum(s == "WARN" for _, s, _ in results)
    print(f"\n{fails} FAIL, {warns} WARN")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
