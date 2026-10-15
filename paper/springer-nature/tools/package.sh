#!/bin/sh
# Build the Springer submission zip and prove it compiles on its own.
#
# Springer's systems want a flat zip (no folders) holding everything needed
# to compile with pdflatex: the clean .tex, .bib + .bbl + .bst, the class
# file, every figure, and the compiled PDF for reference. The review copy
# (forgeflow-springer-marked.*), tools/ and review/ are never shipped.
set -e
cd "$(dirname "$0")/.."
TINY="$HOME/Library/TinyTeX/bin/universal-darwin"
[ -d "$TINY" ] && PATH="$TINY:$PATH"

sh tools/build.sh

ZIP=forgeflow-springer-submission.zip
FILES="forgeflow-springer.tex forgeflow-references.bib forgeflow-springer.bbl \
sn-mathphys-num.bst sn-jnl.cls fig1.png fig2.png fig3.png fig4.png fig5.png \
fig6.png fig7.pdf forgeflow-springer.pdf"
rm -f "$ZIP"
# shellcheck disable=SC2086
zip -q -X -j "$ZIP" $FILES

# Simulated submission: compile from the unzipped package alone.
D=$(mktemp -d)
unzip -q "$ZIP" -d "$D"
(
  cd "$D"
  rm forgeflow-springer.pdf forgeflow-springer.bbl
  pdflatex -interaction=nonstopmode -halt-on-error forgeflow-springer.tex >/dev/null
  bibtex forgeflow-springer >/dev/null
  pdflatex -interaction=nonstopmode -halt-on-error forgeflow-springer.tex >/dev/null
  pdflatex -interaction=nonstopmode -halt-on-error forgeflow-springer.tex >/dev/null
  if grep -nE '^!|undefined' forgeflow-springer.log; then
    echo "standalone compile FAILED"; exit 1
  fi
  echo "standalone compile OK: $(grep -oE '[0-9]+ pages' forgeflow-springer.log)"
)
rm -rf "$D"
unzip -l "$ZIP"
