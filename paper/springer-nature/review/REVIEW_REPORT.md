# Phase 2 review report: ForgeFlow manuscript vs. Springer Nature guidelines

Reviewer: Agent 1 (Springer Nature Guidelines Reviewer), 16 Oct 2026.
Inputs:
- `forgeflow-springer-marked.tex` (master)
- `forgeflow-springer.tex` (generated)
- `forgeflow-references.bib`
- both PDFs
- `review/CHANGES.md`
- `review/PROJECT_FACTS.md`

Checklist: `review/GUIDELINES_CHECKLIST.md` (G01–G66).

**Verdict: technically compliant.** Every Mandatory-general check passes. Submission still waits on the author items listed in "Ready for submission" at the end. One of them, K09 (concurrent IEEE submission), is a hard blocker.

## Headline results

| Check | Result |
|---|---|
| Build (`tools/build.sh`) | Both PDFs: 26 pp, 0 errors, 0 undefined refs/cites, 0 overfull boxes |
| Fidelity (`tools/fidelity_check.py`) | **0 unmarked differences**. Draft tokens 8 674 = reconstructed 8 674; similarity 1.0000. |
| Fidelity-tool audit | The 4 Content Author edits are **confirmed sound**. They only remove rendering noise on both sides, and the title is now compared, which is stricter. The alt-text filter drops exactly 7 draft lines: six `[]` image placeholders and Fig. 7's `[Function-level repair results]`. Mutation test: 4 planted unmarked changes (title word, caption word, Table 4 number, list item) were all reported. |
| Guideline runner (`review/guideline_checks.py`) | 0 FAIL, 1 WARN. The WARN is justified: the added postcode `412115` sits inside `\chg`, and it is not a draft number. |
| ASCII (`grep -nP '[^\x00-\x7F]'` on both .tex files and the .bib) | No output |
| Fonts (`pdffonts`, both PDFs) | **No Type 3.** All text fonts are Computer Modern Type 1, including cm-super `SFRM1000` for the bullet. The one other font is Times New Roman, embedded inside `fig7.pdf` (matplotlib artwork, fully embedded). Bold maths CMMIB10 is gone after R01. |
| Simulated submission (G57) | The flat file set (.tex, .bib, .bst, .cls, 6 PNG, fig7.pdf) compiles from scratch: 26 pp, 0 errors. The `.bbl` regenerates byte-identically. |
| Change marks | 29 macros at 20 sites: `\chg` 11, `\del` 4, `\fix` 9, `\fixdel` 5. These are C01–C09, F01–F08 and R02 (3 sites). |

## Change-mark audit (step 3)

**Format marks (C01–C09).**
- All are correctly typed as `\chg`/`\del` and minimal: C02, C04–C08, C09.
- C03 ("A–E" → "2.1–2.5") is a cross-reference conversion. It is marked so that the fidelity tool matches.
- C01 (the short title) is **not strictly necessary**. sn-jnl v3.1 prints no running head, and the title is absent from the PDF metadata. It is kept as the coordinator's decision; it has zero printed effect.

**Factual marks (F01–F08).** Each was verified in the code. All are correctly typed as `\fix`/`\fixdel`.

| ID | Claim | Verified at | Result |
|---|---|---|---|
| F01 | Post-repair rate is not pass@1 | `eval/results/REPORT.md:17`: "it is a self-repair number, not a pass@1" | Correct |
| F02 | Intent actions include unskip and reset | `backend/app/orchestrator/schemas.py:80-97` (`IntentAction` has five members) | Correct. Nuance: unskip and reset are answered by the conductor, not a stage handler (flag 15 in CHANGES). |
| F03 | No JUnit parser | `backend/app/testing/parsers.py:194-210`: Vitest, Jest and Playwright JSON only; `grep -ri junit backend/app` finds nothing | Correct |
| F04 | Over-size parts are truncated or dropped, each recorded | `backend/app/agents/repair_context.py:237-240,298-306` (`truncate_head`; dropped when over budget; each entry in `trimmed`) | Correct |
| F05 | 67 phases, 14 epics | `git show c5a7835:plans/README.md`: phases 01–67, epics 0–13 | Correct |
| F06 | Diff and import-graph parts can be exercised only at Level 2 | `backend/app/eval/runner.py:195` uses the base `RepairContextAnalyzer` (`stages/repair.py:224`); import-graph widening exists only in `SuiteRepairContextAnalyzer`, used at `testing/repair_router.py:107` | **Partly correct.** The import-graph part is not reachable at Level 2 either. Flagged with optional wording (CHANGES item 14); not changed, because D3 was a coordinator decision. |
| F07 | HumanEval-TS is pinned by content hash | `backend/app/eval/bench/datasets.py:36-42` | Correct |
| F08 | Concurrency 8, and 12 for BFCL | `backend/app/eval/bench/code.py:218` (8), `bfcl.py:397` (12); no call-site override (`grep concurrency backend/app/eval`) | Correct |

**Declarations.** Checked against the evidence:
- *Competing interests* matches the B.Tech report: the invention disclosure was "submitted for filing… not yet filed".
- *Data* and *Code* match the evidence: per-item JSON files are gitignored, and the README says "Proprietary / … Not for redistribution".
- *Author contribution* assigns only roles with repository or report evidence (commit areas, Fig. 7 script `ebdf806`, figures `768cb2b`). Payal Kadam's "Writing – review & editing" rests on the acknowledgement text, so the authors should confirm it. Nobody is credited with Conceptualization, Methodology or Writing – original draft.

## Bibliography and numbers (step 4)

- Each of the 32 rendered references (pdftotext of the clean PDF) was compared token by token with the draft list.
  - Every author, title word, venue element, volume, number, article number, page range, year, DOI, arXiv ID, URL and access date is present.
  - The only differences are style effects: months are not printed by sn-mathphys-num, and labels such as "vol."/"pp."/"Art. no." and punctuation are restyled.
- Title capitalisation: every capitalised word in a draft title appears verbatim in the PDF (G39a), and "de Oliveira Pinto" is intact.
- `.bbl` order equals first-citation order, and equals the draft's [1]–[32].
- Numbers:
  - Fidelity is 0 differences, which covers every digit in the prose, tables and captions.
  - No `\chg`/`\del` touches a draft number.
  - Every `\fix` digit change is verified (65 → 67).
  - `fig7.pdf` data equals `paper/figures/fig7-repair-results.py` and Table 4.

## Visual inspection (step 5)

- The title page is correct: four authors, a shared affiliation, the corresponding-author line, and the abstract.
- Floats appear in sequence near their citations: Table 1 p.6; Figs. 1–4 pp.7–10; Fig. 5 p.13; Fig. 6 p.14; Table 2 p.15; Table 3 p.17; Tables 4–5 p.18; Fig. 7 p.19; Table 6 p.20; Table 7 p.21. No table is split.
- The Declarations are on p.23; References run pp.23–26.
- The clean PDF has no stray markup or colour. In the marked PDF, R02's marks show in the author block.
- **Two figure findings, both flagged, because figure files are outside the reviewer's write scope:**
  - (a) Figs. 2, 3, 5 and 6 print "(Fig. 3)" for the repair-loop diagram, which is **Fig. 4**. This comes from the draw.io sources' old numbering and is pre-existing in the draft.
  - (b) Figs. 2 and 3 have box lettering of about 5–6 pt at full width (advisory 8–12 pt). They are legible when zoomed.

## Refinements applied (round 1 of 2; round 2 not needed)

- **R01:** `{\unboldmath$p < 0.001$}` in the abstract. This fixes the bold-maths leak from `\Authorfont`, with no wording change.
- **R02:** marked M01/M02 in the review copy: `\del{Dr. }`, `\del{CSE}\chg{Computer Science and Engineering}`, `\chg{\postcode{412115}, \state{Maharashtra}, }`. The clean output is unchanged.
- **R03:** the runner's G06 font check now recognises cm-super and figure-embedded fonts. The checklist now names `forgeflow-references.bib` and `fig7.pdf` (G27/G40/G56/G61).

All three are logged in `review/CHANGES.md`. After them: build passes, fidelity is 0, and the runner reports 0 FAIL.

## G01–G66

| ID | Status | Note |
|---|---|---|
| G01 | PASS | cls and bst byte-identical to the template |
| G02 | PASS | `[pdflatex,sn-mathphys-num]{sn-jnl}` |
| G03 | PASS | Numbered style reproduces [1]–[32]; switch only if the chosen journal mandates author-year |
| G04 | PASS | Single .tex |
| G05 | PASS | 0 errors, 0 undefined, no `??` |
| G06 | PASS | No Type 3; CM and cm-super only. Times New Roman only inside `fig7.pdf` (runner fixed, R03). |
| G07 | PASS | Template packages only; `ulem` only in the markup block |
| G08 | PASS | ASCII-only; no "Missing character" |
| G09 | PASS | No ¡/¿ in the PDF |
| G10 | PASS | No spacing or colour commands in the clean file |
| G11 | PASS | No markup or placeholders in the clean file |
| G12 | PASS | Exact draft title. The short title (C01) is not printed and not strictly needed; kept as the coordinator's decision. |
| G13 | FIXED | R02: the honorific removal is now marked. Names and order match the draft. |
| G14 | PASS / FLAG | Payal Kadam is the corresponding author (coordinator); authors confirm, and confirm each given/family split |
| G15 | FIXED / FLAG | R02 marks the CSE expansion and the address additions; authors verify |
| G16 | FIXED / FLAG | R01 fixes the bold p-value. 424 words, against a 150–250 guide: journal-dependent. |
| G17 | FLAG | 10 keywords; many journals ask for 4–6 |
| G18 | PASS | |
| G19 | PASS | Contributions are an enumerate list |
| G20 | PASS | Ten numbered sections, ≤2 levels, draft headings |
| G21 | PASS | All refs through `\ref`; no Roman numerals left |
| G22 | PASS | |
| G23 | PASS | |
| G24 | PASS | No footnotes |
| G25 | FLAG | Abbreviations not defined at first use (K17) |
| G26 | FLAG | "artefact" (1x) vs "artifact" (K16) |
| G27 | PASS | `fig1-6.png` + `fig7.pdf`, flat, exact names |
| G28 | PASS / FLAG | Figs. 1–6 ≥930 dpi; Fig. 7 is vector. Figs. 2–3 lettering is small (advisory). |
| G29 | PASS / FLAG | Captions verbatim. Final periods kept (K15). **Stale in-artwork "(Fig. 3)" in Figs. 2, 3, 5, 6 should read Fig. 4.** |
| G30 | PASS | Figs. 1–7 first cited in order (C04–C08) |
| G31 | PASS | Floats in sequence near their citations |
| G32 | FLAG | Authors confirm no generative-AI imagery (sources are draw.io and matplotlib), and IEEE figure rights if the conference paper is published |
| G33 | PASS | 7 tables, booktabs, caption above, no `\hline` |
| G34 | PASS | Tables 1–7 first cited in order (C02) |
| G35 | PASS | Cells verbatim (fidelity); symbols converted |
| G36 | PASS | 0 overfull boxes |
| G37 | PASS | No table footnotes, as in the draft |
| G38 | PASS | 32 bibitems in draft order |
| G39 | PASS | Field by field; capitalisation and name particle intact; months not printed (style) |
| G40 | FLAG | 12 arXiv refs without DOIs; IEEE journal abbreviations; [6] cites the arXiv v1 title. All 16 DOIs and 12 arXiv IDs resolve. |
| G41 | PASS | No `\citet`; clean `.blg` |
| G42 | PASS | `.bbl` generated and reproducible; include it in the zip, and paste it into the .tex if the system requires |
| G43 | PASS | |
| G44 | PASS | `\section*{Declarations}`, all 8 headings, `\chg`-marked |
| G45 | FLAG | "No support received": authors confirm (the ₹6k budget source is unknown) |
| G46 | PASS / FLAG | Patent-disclosure statement is accurate to the evidence; authors confirm filing status and publication timing |
| G47 | PASS | Not applicable (no human or animal participants) |
| G48 | PASS | Not applicable |
| G49 | PASS / FLAG | Public benchmarks are cited; per-item results "on request". Consider a DOI deposit. |
| G50 | PASS | Not applicable |
| G51 | PASS / FLAG | Proprietary, on request; it will be read against the "reproducible make eval" claim |
| G52 | FLAG | Evidenced roles only; missing Conceptualization, Methodology and Writing – original draft; Kadam's W–R&E to confirm |
| G53 | FLAG | No AI-use statement (coordinator: author decision). The codebase was built with an AI coding agent, and a "humanized" .docx exists. |
| G54 | FLAG (**blocker**) | Paper "submitted to an IEEE conference and under review" (K09) |
| G55 | FLAG | Authors confirm authorship and order |
| G56 | N/A | The coordinator builds the zip; the expected contents in the checklist are updated |
| G57 | PASS | Simulated flat-folder compile: 26 pp, 0 errors |
| G58 | PASS | |
| G59 | PASS | 0 unmarked differences; tool edits audited |
| G60 | PASS | 20 sites audited; types correct; C01 not strictly needed (kept) |
| G61 | PASS | Numbers unchanged except the verified `\fix` 65 → 67; fig7 data equals Table 4 |
| G62 | PASS | All 32 references checked against the draft |
| G63 | PASS | ASCII grep empty |
| G64 | PASS | Names, e-mails and order match draft lines 3–29 |
| G65 | PASS / FLAG | Clean PDF has no markup. See G29 (in-figure references) and G28 (lettering). |
| G66 | PASS | 0 FAIL, 1 justified WARN |

## Ready for submission once the authors resolve

1. **K09, blocker:** withdraw the IEEE submission or wait for its decision. If it is published, disclose the journal version as an extension (cover letter, citation, IEEE figure rights).
2. ~~Re-export Figs. 2, 3, 5 and 6 with "(Fig. 4)" in place of "(Fig. 3)".~~ Resolved by the coordinator (CHANGES F09, glyph-level correction of the published PNGs). Optional: enlarge the lettering in Figs. 2–3.
3. **Confirm the Declarations:**
   - funding (none);
   - patent-disclosure filing status and publication timing;
   - data and code availability (on request vs a DOI deposit);
   - author contributions (add Conceptualization, Methodology and Writing – original draft as appropriate).
4. **AI-use statement** (G53): decide whether to disclose the AI coding agent and any writing support.
5. **Open factual items:**
   - D1: test counts 2 323 vs 2 450.
   - D7: guard precedence.
   - D3 residual (import-graph part).
   - N3: "first two" vs "first two to complete".
   - Failure-mode (ii) evidence.
6. **Front matter:** confirm Payal Kadam as corresponding author, each given/family split, the expanded department name, and the postcode/state.
7. **After choosing the journal:**
   - apply its limits to the abstract (424 words) and keywords (10), cutting with `\del` only;
   - check the reference style (numbered), the Declarations heading wording, `referee`/`lineno`, and whether it requires a structured abstract.
8. **Optional:** arXiv DOIs, LTWA journal abbreviations, the [6] title version, caption end periods, "artefact"/"artifact", and abbreviation definitions.


## Coordinator follow-up (16 Oct 2026)

- **D3 residual (F06):** the sentence now reads "…the diff can be exercised only at Level 2, and the import-graph walk at neither level", matching `eval/runner.py:195` (base analyser: diff, no import walk) and `testing/repair_router.py:107` (the only caller of the widening analyser).
- **Figure cross-references (F09):** "(Fig. 3)" → "(Fig. 4)" inside Figs. 2, 3, 5 and 6 by a single-glyph replacement (`tools/fix_fig_refs.py`); re-exporting from the stale `.drawio` sources was tested and rejected because it changes the approved layout.
- **Re-run after both changes:** build 26 pp / 0 undefined / 0 overfull (both PDFs); fidelity 0 unmarked differences; `guideline_checks.py` 0 FAIL, 1 WARN (the postcode, as before); ASCII grep empty; `pdffonts`: 19 Type 1 + the TrueType face inside `fig7.pdf`, no Type 3; `tools/package.sh` standalone compile of the unzipped package OK (26 pp).

## Authors' rewrite round (16 Oct 2026)

After the Turnitin AI-writing report (26 % flagged), the authors rewrote the 11 large flagged passages (`review/authors-rewrites-2026-10-16.docx`). These now replace the old text in 19 `\rw` blocks. The authors chose to correct only the factual errors, reversed meanings and broken sentences in the rewrites; every correction is marked (CHANGES §4, W01–W11).

Re-run after the round:
- Build: 27 pp, 0 undefined, 0 overfull (both PDFs).
- `fidelity_check.py`: 0 unmarked differences against the 4 Oct draft outside the rewritten passages.
- `rewrite_check.py`: 1 574 = 1 574 tokens, 0 unmarked differences from the authors' docx.
- `guideline_checks.py`: 0 FAIL, 1 WARN (the postcode, as before).
- `.bbl` byte-identical (reference numbering unchanged); ASCII grep empty; no markup in the clean `.tex`.
- 83 % of the authors' rewrite words are kept in place.

The text was not re-submitted to Turnitin. Re-check it before submission if an AI-writing score is required.
