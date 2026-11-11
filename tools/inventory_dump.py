"""Stage 1 (server dump): list the blog posts, events and news items in the site dump.

Usage: inventory_dump.py DUMP_DIR OUT.json [--types blog-entry,event,news-item]

DUMP_DIR is the unzipped cis-india-archive-YYYY-MM-DD folder (it holds site/ and nginx/).
Every rendered page in the dump is site/<path>/index.html; its Plone type is in the
<body> class (portaltype-...), its canonical URL in <base href>, and most pages carry
hidden data-pagefind-* spans with clean metadata (type, tags, authors, date).
Pages reachable under two paths (e.g. /website/...) are kept once, by canonical URL.
Writes the same inventory format convert.py reads from a crawl.
"""
import json, os, re, sys
from urllib.parse import urlparse, unquote
from bs4 import BeautifulSoup

DUMP, OUT = sys.argv[1], sys.argv[2]
TYPES = {"blog-entry": "Blog Entry", "event": "Event", "news-item": "News Item"}
if "--types" in sys.argv:
    want = sys.argv[sys.argv.index("--types") + 1].split(",")
    TYPES = {k: v for k, v in TYPES.items() if k in want}
SITE = os.path.join(DUMP, "site")
TYPE_RE = re.compile(rb'<body[^>]*class="[^"]*portaltype-([a-z-]+)')
BASE_RE = re.compile(rb'<base href="([^"]+)"')


def text(el):
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


def iso_to_listing(d):
    """convert.py's parse_date also takes ISO; keep ISO."""
    return d


def scan(path, rel):
    head = open(path, "rb").read()
    m = TYPE_RE.search(head)
    if not m or m.group(1).decode() not in TYPES:
        return None
    base = BASE_RE.search(head)
    url = base.group(1).decode() if base else "https://cis-india.org/" + rel
    soup = BeautifulSoup(head, "lxml")
    title = text(soup.select_one("#parent-fieldname-title")) or text(soup.title).split(" — ")[0]
    filt = {}
    for s in soup.select("[data-pagefind-filter]"):
        k, _, v = s["data-pagefind-filter"].partition(":")
        filt.setdefault(k, []).append(v)
    date = None
    meta = soup.select_one('[data-pagefind-meta^="date:"]')
    if meta:
        date = meta["data-pagefind-meta"].split(":", 1)[1]
    authors = filt.get("Posted by / author", [])
    if not authors:   # pages without the hidden spans: author portlet links to /author/<id>
        authors = [text(a) for a in soup.select('h5 a[href*="/author/"]') if text(a)]
    tags = filt.get("Tags") or [text(a) for a in soup.select("#category a.link-category") if text(a)]
    if m.group(1) == b"event" and not date:
        st = soup.select_one("abbr.dtstart")
        if st and st.get("title"):
            date = st["title"][:10]
    desc = soup.select_one("p.sectionIntro, p.articleIntro")
    return {
        "url": url.rstrip("/"),
        "title": title,
        "type": TYPES[m.group(1).decode()],
        "authors": list(dict.fromkeys(authors)),
        "published": date,
        "modified": None,
        "tags": list(dict.fromkeys(tags)),
        "listing_description": text(desc),
        "html": os.path.abspath(path),
        "dump_path": "/" + rel,
        "attachments": [],
    }


items = {}
for root, dirs, files in os.walk(SITE):
    rel = os.path.relpath(root, SITE)
    if rel == "pagefind" or rel.startswith("pagefind/") or "/__page/" in "/" + rel + "/":
        dirs[:] = []
        continue
    if "index.html" not in files or rel == ".":
        continue
    it = scan(os.path.join(root, "index.html"), rel)
    if not it:
        continue
    canon = unquote(urlparse(it["url"]).path)
    prev = items.get(canon)
    # keep the copy that lives at the canonical path; otherwise the first seen
    if prev is None or ("/" + rel == canon and prev["dump_path"] != canon):
        items[canon] = it

# A few pages report a folder as their canonical URL (title "[···]", empty body). A page at
# a folder's path would swallow every item below it in Hugo, so drop those.
parents = {os.path.dirname(c) for c in items}
# /search in the dump is its own Pagefind search page, not a post; the archive has its own.
dropped = [c for c in items if c in parents or c == "/search" or items[c]["title"] in ("", "[···]")]
for c in dropped:
    print("skipped (folder, not an item):", c, file=sys.stderr)
    del items[c]
out = sorted(items.values(), key=lambda i: (i["published"] or "", i["url"]), reverse=True)
counts = {}
for i in out:
    counts[i["type"]] = counts.get(i["type"], 0) + 1
json.dump({"source": os.path.abspath(DUMP), "counts": counts, "items": out}, open(OUT, "w"), indent=1, ensure_ascii=False)
print(counts)
