# ForgeFlow: Springer Nature submission guide

For the submitting author (Dr. Payal Kadam, corresponding author).

## What to upload

**`forgeflow-springer-submission.zip`** is the complete Springer upload. It is a flat zip with no folders, and it compiles with `pdflatex` as Springer requires. Its 13 files:

| File | What it is |
|---|---|
| `forgeflow-springer.tex` | The manuscript: a single file in the Springer `sn-jnl` template, with no colour markup |
| `forgeflow-references.bib`, `forgeflow-springer.bbl`, `sn-mathphys-num.bst` | References: the BibTeX source, the pre-built bibliography and the Springer numbered style |
| `sn-jnl.cls` | The Springer Nature class file (template v3.1, December 2024) |
| `fig1.png` – `fig6.png`, `fig7.pdf` | Figures 1–7 |
| `forgeflow-springer.pdf` | The compiled manuscript, for reference (27 pages) |

- **Snapp** (most Springer journals): upload the zip as it is.
- **Editorial Manager**: upload the files individually. Item-type names vary by journal; typically use "Manuscript" for the .tex, "Figure" for each figure, and "LaTeX supplementary file" for the .bib, .bbl, .bst and .cls.

The unzipped package was compiled from scratch with no other files present: 0 errors and 0 undefined references.

## Before you submit

1. **The IEEE conference submission must be resolved first.** The project report says this paper is under review at an IEEE conference. Springer forbids submitting the same work to two venues at once, so withdraw it or wait for the decision. If the conference accepts it, the journal version must cite the conference paper, extend it substantially, and disclose the earlier version in the cover letter.
2. **Choose the journal, then check its limits.** The abstract (about 420 words) and the 10 keywords are kept exactly as in the draft. Many Springer journals cap the abstract at 150–250 words and ask for 4–6 keywords.
3. **Confirm the blue Declarations text in the review copy.** Every statement was written from repository evidence:
   - Funding: none.
   - Competing interests: the patent disclosure, and its filing status.
   - Data and code: available on request.
   - The author-contribution roles.
4. **Decide on an AI-use statement.** Springer requires one if generative AI was used for anything beyond copy-editing.
5. **Check the author block:** names, emails, the corresponding author, and the department and address (Pune 412115, Maharashtra). Dr. Ranjeet Bidwe was added as fifth author on 17 Oct. His e-mail is still missing from the manuscript, and his contribution role ("Writing – review & editing") needs confirming.

## Review copy (for the authors, not for Springer)

`forgeflow-springer-marked.pdf` shows every difference from the 4 Oct 2026 draft:

- **Blue:** text added or changed to fit the Springer format.
- **Red strike-through:** text removed for the format.
- **Orange:** a factual correction made against the implementation.
- **Orange strike-through:** text removed as a factual correction.
- **Green:** the authors' own rewrite of a passage flagged by the Turnitin AI-writing report (16 Oct 2026). Orange marks inside green text are corrections to that rewrite (factual errors, reversed meanings and broken sentences only).

All other text is the draft word for word. `review/CHANGES.md` lists every change (section 4 covers the 16 Oct rewrites) with its reason and evidence, and `review/REVIEW_REPORT.md` records the guideline check.

## Rebuilding after edits

Edit **only** `forgeflow-springer-marked.tex`. Then run:

```sh
sh tools/package.sh
```

This regenerates the clean .tex, both PDFs and the zip, and test-compiles the zip. The script needs TeX Live: TinyTeX in `~/Library/TinyTeX` or any `pdflatex` on PATH.

After changing any wording, also run:

```sh
python3 tools/fidelity_check.py
```

It confirms that every deviation from the draft is still marked. Also run:

```sh
python3 tools/rewrite_check.py
```

It confirms that the authors' 16 Oct rewrites (`review/authors-rewrites-2026-10-16.docx`) are reproduced word for word, apart from the marked corrections.
