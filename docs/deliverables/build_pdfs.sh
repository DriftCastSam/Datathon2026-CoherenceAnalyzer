#!/usr/bin/env bash
# Regenerate the HTML and rebuild the two PDF deliverables (needs google-chrome or chromium).
set -euo pipefail
cd "$(dirname "$0")"
python make_onepager.py
python make_architecture.py
CHROME="$(command -v google-chrome || command -v chromium || command -v chromium-browser)"
for f in one-pager architecture-aws; do
  "$CHROME" --headless=new --disable-gpu --no-pdf-header-footer --print-to-pdf="$f.pdf" "file://$PWD/$f.html" 2>/dev/null
  echo "$f.pdf: $(pdfinfo "$f.pdf" 2>/dev/null | awk '/^Pages/{print $2}') page(s)"
done
