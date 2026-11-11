#!/bin/sh
# Build the static site into ./site/public.
#   theme/   - Hugo config, layouts, CSS, section pages (hand-written)
#   archive/ - converted content + attachments (output of tools/convert.py)
# Usage: tools/build.sh [extra-config.toml]   e.g. theme/preview.toml for .html URLs
# ARCHIVE=/path/to/archive overrides ./archive (e.g. a full conversion kept outside git).
# FILES_BASE=https://files.example.org: attachments live in a bucket (see tools/upload_files_r2.sh);
#   /files/* is redirected there via public/_redirects and left out of public/.
# Uses $HUGO if set, else ./bin/hugo if present, else hugo on PATH (needs Hugo 0.140.2 extended).
set -e
S=$(cd "$(dirname "$0")/.." && pwd)
HUGO=${HUGO:-$( [ -x "$S/bin/hugo" ] && echo "$S/bin/hugo" || echo hugo )}
rm -rf "$S/site" && mkdir -p "$S/site"
ARCHIVE=${ARCHIVE:-$S/archive}
cp -r "$S/theme/." "$S/site/"
cp -rl "$ARCHIVE/." "$S/site/" 2>/dev/null || cp -r "$ARCHIVE/." "$S/site/"   # hard links save GBs of copying
cd "$S/site"
"$HUGO" --minify -d public ${1:+--config hugo.toml,$S/$1}
find public -path '*/page/1' -prune -exec rm -rf {} + ; find public -path '*/page/1.html' -delete   # pagination redirect stubs
npx --yes pagefind@1.3.0 --site public
if [ -n "$FILES_BASE" ]; then
  rm -rf public/files
  printf '/files/* %s/files/:splat 301\n' "${FILES_BASE%/}" > public/_redirects
fi
echo "files: $(find public -type f | wc -l)  size: $(du -sh public | cut -f1)"
