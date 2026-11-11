#!/bin/sh
# Build the static site into ./site/public.
#   theme/   - Hugo config, layouts, CSS, section pages (hand-written)
#   archive/ - converted content + attachments (output of tools/convert.py)
# Usage: tools/build.sh [extra-config.toml]   e.g. theme/preview.toml for .html URLs
# ARCHIVE=/path/to/archive overrides ./archive (e.g. a full conversion kept outside git).
# Attachments (/files/*) are served from R2 by functions/files/[[path]].js (bucket bound as FILES).
# FILES_BASE=https://files.example.org instead redirects /files/* to another host via _redirects.
# FILES_FALLBACK (default: the Internet Archive item made by tools/upload_files_ia.py) is where
# /files/* redirects when the Function's free daily quota runs out; FILES_FALLBACK= turns it off.
# Uses $HUGO if set, else ./bin/hugo if present, else hugo on PATH (needs Hugo 0.140.2 extended).
set -e
S=$(cd "$(dirname "$0")/.." && pwd)
HUGO=${HUGO:-$( [ -x "$S/bin/hugo" ] && echo "$S/bin/hugo" || echo hugo )}
rm -rf "$S/site" && mkdir -p "$S/site"
ARCHIVE=${ARCHIVE:-$S/archive}
cp -r "$S/theme/." "$S/site/"
cp -rl "$ARCHIVE/." "$S/site/" 2>/dev/null || cp -r "$ARCHIVE/." "$S/site/"   # hard links save GBs of copying
FILES_FALLBACK=${FILES_FALLBACK-https://archive.org/download/cis-india-org-attachments/files}
export HUGO_PARAMS_FILESFALLBACK="${FILES_FALLBACK%/}"   # used by the 404 page
cd "$S/site"
"$HUGO" --minify -d public ${1:+--config hugo.toml,$S/$1}
find public -path '*/page/1' -prune -exec rm -rf {} + ; find public -path '*/page/1.html' -delete   # pagination redirect stubs
npx --yes pagefind@1.3.0 --site public
if [ -n "$FILES_BASE" ]; then   # attachments on another host: redirect to it
  rm -rf public/files
  printf '/files/* %s/files/:splat 301\n' "${FILES_BASE%/}" > public/_redirects
else                            # attachments from R2 via functions/files/: only /files/* runs the Function
  printf '{"version":1,"include":["/files/*"],"exclude":[]}\n' > public/_routes.json
  # _redirects never applies while the Function serves /files/*. Once the free daily quota is used
  # up, Pages "fails open" (the default) and handles /files/* as static: then this rule sends the
  # request to the Internet Archive copy until the quota resets at midnight UTC.
  [ -z "$FILES_FALLBACK" ] || printf '/files/* %s/:splat 302\n' "${FILES_FALLBACK%/}" > public/_redirects
fi
echo "files: $(find public -type f | wc -l)  size: $(du -sh public | cut -f1)"
