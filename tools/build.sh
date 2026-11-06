#!/bin/sh
# Build the static site into ./site/public.
#   theme/   - Hugo config, layouts, CSS, section pages (hand-written)
#   archive/ - converted content + attachments (output of tools/convert.py)
# Usage: tools/build.sh [extra-config.toml]   e.g. theme/preview.toml for .html URLs
# Uses $HUGO if set, else ./bin/hugo if present, else hugo on PATH (needs Hugo 0.140.2 extended).
set -e
S=$(cd "$(dirname "$0")/.." && pwd)
HUGO=${HUGO:-$( [ -x "$S/bin/hugo" ] && echo "$S/bin/hugo" || echo hugo )}
rm -rf "$S/site" && mkdir -p "$S/site"
cp -r "$S/theme/." "$S/site/"
cp -r "$S/archive/." "$S/site/"
cd "$S/site"
"$HUGO" --minify -d public ${1:+--config hugo.toml,$S/$1}
find public -path '*/page/1' -prune -exec rm -rf {} + ; find public -path '*/page/1.html' -delete   # pagination redirect stubs
npx --yes pagefind@1.3.0 --site public
echo "files: $(find public -type f | wc -l)  size: $(du -sh public | cut -f1)"
