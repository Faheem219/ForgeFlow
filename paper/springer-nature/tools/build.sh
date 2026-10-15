#!/bin/sh
# Build both PDFs with real pdflatex + bibtex (Springer's Snapp requires the
# manuscript to compile with pdflatex).
#
#   forgeflow-springer-marked.pdf  review copy, changes from the draft highlighted
#   forgeflow-springer.pdf         clean copy, exactly what Springer receives
#
# TeX Live: uses TinyTeX at ~/Library/TinyTeX if present, else pdflatex on PATH.
set -e
cd "$(dirname "$0")/.."
TINY="$HOME/Library/TinyTeX/bin/universal-darwin"
[ -d "$TINY" ] && PATH="$TINY:$PATH"

python3 tools/make_clean.py forgeflow-springer-marked.tex forgeflow-springer.tex

for doc in forgeflow-springer-marked forgeflow-springer; do
  pdflatex -interaction=nonstopmode -halt-on-error "$doc.tex" >/dev/null || { grep -A4 '^!' "$doc.log"; exit 1; }
  bibtex "$doc" >/dev/null || { cat "$doc.blg"; exit 1; }
  pdflatex -interaction=nonstopmode -halt-on-error "$doc.tex" >/dev/null
  pdflatex -interaction=nonstopmode -halt-on-error "$doc.tex" >/dev/null
  undef=$(grep -cE 'undefined|Citation .* undefined' "$doc.log" || true)
  over=$(grep -c 'Overfull' "$doc.log" || true)
  pages=$(grep -oE 'Output written on .* \(([0-9]+) pages' "$doc.log" | grep -oE '[0-9]+ pages')
  echo "$doc.pdf: $pages, undefined refs/cites: $undef, overfull boxes: $over"
done
