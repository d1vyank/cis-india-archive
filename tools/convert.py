"""Stage 2: convert Plone pages (from a crawl or from the server dump) into Hugo content.

Usage: convert.py CRAWL_DIR SITE_DIR        (CRAWL_DIR holds inventory.json, html/, files/)
       convert.py INVENTORY.json SITE_DIR   (inventory from inventory_dump.py; files come
                                            from the dump folder named in it)
Set LINK_BASE to change where links to pages outside the archive point
(default https://cis-india.org), and FILES_BASE to serve attachments from another host.
Each item becomes SITE_DIR/content/<original path>/index.html (cleaned HTML body
with YAML front matter), so the archive keeps the original URL paths.
Attachments are copied to SITE_DIR/static/files/<original path> and links rewritten.
Writes SITE_DIR/../convert_report.json with missing files and stats.
"""
import json, os, re, shutil, sys
from datetime import datetime
from urllib.parse import urljoin, urlparse, unquote, quote
from bs4 import BeautifulSoup, Comment, NavigableString
import yaml

CRAWL, SITE = sys.argv[1], sys.argv[2]
HOSTS = ("cis-india.org", "www.cis-india.org")
SCALE_RE = re.compile(r"/(image_[a-z]+|image/image_view_fullscreen|@@images/.*|at_download/file|view)$")
KEEP_ATTRS = {"href", "src", "alt", "title", "colspan", "rowspan", "width", "height", "id", "name"}

LINK_BASE = os.environ.get("LINK_BASE", "https://cis-india.org").rstrip("/")
# FILES_BASE=https://files.example.org serves attachments from elsewhere (e.g. an R2 bucket
# holding static/files/); they are still copied to static/files/ for upload.
FILES_URL = os.environ.get("FILES_BASE", "").rstrip("/") + "/files"
if CRAWL.endswith(".json"):
    inv = json.load(open(CRAWL)); CRAWL = os.path.dirname(os.path.abspath(CRAWL))
else:
    inv = json.load(open(f"{CRAWL}/inventory.json")) if os.path.exists(f"{CRAWL}/inventory.json") else None
items = inv["items"] if inv else json.load(open(f"{CRAWL}/listing.json"))
DUMP = inv.get("source") if inv else None
archived_paths = {unquote(urlparse(i["url"]).path).rstrip("/") for i in items}
missing, stats = set(), {"items": 0, "skipped": 0, "files_linked": 0, "files_copied": 0, "bytes_copied": 0}
TYPE_MAP = {"Event": "event", "News Item": "news"}


class NginxMap:
    """The dump's nginx maps: "path@b_start" -> target. Plain keys are case-insensitive,
    "~" keys are regexes."""
    LINE = re.compile(r'^"((?:[^"\\]|\\.)*)"\s+"((?:[^"\\]|\\.)*)";$')

    def __init__(self, path):
        self.exact, self.regex = {}, []
        for line in open(path, encoding="utf-8"):
            m = self.LINE.match(line.strip())
            if not m:
                continue
            k, v = (re.sub(r"\\(.)", r"\1", g) for g in m.groups())
            if k.startswith("~"):
                self.regex.append((re.compile(k[1:]), v))
            else:
                self.exact.setdefault(k.lower(), v)

    def get(self, key):
        v = self.exact.get(key.lower())
        if v is None:
            v = next((val for r, val in self.regex if r.match(key)), None)
        return v


if DUMP:
    DUMP_SITE = os.path.join(DUMP, "site")
    FILES = NginxMap(os.path.join(DUMP, "nginx", "files.map"))
    REDIRECTS = NginxMap(os.path.join(DUMP, "nginx", "redirects.map"))
VIEW_RE = re.compile(r"^(/.+?)/(?:view|at_download/[^/]+|@@download/[^/]+|image_view_fullscreen|image_view|file_view)/?$")


def follow_redirects(p):
    """Old Plone paths -> current path, as the dump's server does."""
    for _ in range(6):
        m = VIEW_RE.match(p)
        if m:
            p = m.group(1); continue
        r = REDIRECTS.get(p + "@") if DUMP else None
        if r and urlparse(r).path.rstrip("/") != p:
            p = unquote(urlparse(r).path).rstrip("/") or "/"; continue
        break
    return p


def dump_file(p):
    """(url path, file in the dump) for a downloadable file, or None for pages/missing."""
    f = FILES.get(p + "@")
    cand = os.path.join(DUMP_SITE, f.lstrip("/")) if f else os.path.join(DUMP_SITE, p.lstrip("/"))
    cand = os.path.normpath(cand)
    if not cand.startswith(DUMP_SITE + os.sep) or not os.path.isfile(cand) or cand.endswith("index.html"):
        return None
    return cand


def cf_decode(hexstr):
    k = int(hexstr[:2], 16)
    return "".join(chr(int(hexstr[i:i + 2], 16) ^ k) for i in range(2, len(hexstr), 2))


def parse_date(s):
    if not s:
        return None
    if re.match(r"\d{4}-\d\d-\d\d", s):
        return s[:10]
    for fmt in ("%b %d, %Y %I:%M %p", "%b %d, %Y"):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%dT%H:%M:%SZ" if "M" in s else "%Y-%m-%d")
        except ValueError:
            pass
    return None


def copy_to_static(src, cand):
    dest = f"{SITE}/static/files/{cand}"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if not os.path.exists(dest):
        shutil.copyfile(src, dest)
        stats["files_copied"] += 1; stats["bytes_copied"] += os.path.getsize(src)
    return FILES_URL + "/" + quote(cand)


def local_file_dump(url):
    p = follow_redirects(unquote(urlparse(url).path).rstrip("/"))
    for cand in dict.fromkeys((SCALE_RE.sub("", p), p)):   # prefer the original over a scale
        src = dump_file(cand)
        if not src:
            continue
        cand = cand.strip("/")
        ext = os.path.splitext(src)[1]          # dump stores extensionless URLs as <path>/__self.<ext>
        same = {".jpeg": (".jpg", ".jpeg", ".jpe"), ".jpg": (".jpg", ".jpeg", ".jpe"), ".htm": (".htm", ".html")}
        if os.path.basename(src).startswith("__self") and not cand.lower().endswith(same.get(ext.lower(), (ext.lower(),))):
            cand += ext.lower()
        return copy_to_static(src, cand)
    return None


def local_file(url):
    """Return the site path (/files/...) for a downloaded internal file, or None."""
    if DUMP:
        return local_file_dump(url)
    p = unquote(urlparse(url).path).strip("/")
    for cand in (SCALE_RE.sub("", p), p):
        src = f"{CRAWL}/files/{cand}"
        if os.path.isdir(src) and os.path.isfile(src + "/__index"):
            cand, src = cand + "/__index", src + "/__index"   # crawler's name for a URL that is also a folder
        if os.path.isfile(src):
            dest = f"{SITE}/static/files/{cand}"
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if not os.path.exists(dest):
                shutil.copyfile(src, dest)
            return FILES_URL + "/" + quote(cand)
    return None


def rewrite_url(ref, base, is_img=False):
    if not ref or ref.startswith(("mailto:", "#", "javascript:", "data:")):
        return ref
    ref = re.sub(r"^\.+(?=https?://)", "", ref.strip())   # dump artifact: "..https://cis-india.org/x.png"
    absu = urljoin(base + "/", ref)
    u = urlparse(absu)
    if u.scheme not in ("http", "https") or u.netloc not in HOSTS:
        return ref
    lf = local_file(absu)
    if lf:
        stats["files_linked"] += 1
        return lf + (f"#{u.fragment}" if u.fragment else "")
    path = unquote(u.path).rstrip("/")
    if DUMP:
        path = follow_redirects(path)
    frag = f"#{u.fragment}" if u.fragment else ""
    if path in archived_paths or SCALE_RE.sub("", path) in archived_paths:
        return quote(SCALE_RE.sub("", path)) + "/" + frag
    if is_img or re.search(r"\.(pdf|docx?|odt|pptx?|xlsx?|jpe?g|png|gif)$", path, re.I) or "at_download" in path:
        missing.add(absu)
    return LINK_BASE + quote(path or "/") + (f"?{u.query}" if u.query else "") + frag  # not archived: link out


def clean(fragment, base):
    for c in fragment.find_all(string=lambda t: isinstance(t, Comment)):
        c.extract()
    for el in fragment.find_all(["script", "style", "o:p"]):
        el.decompose()
    # Cloudflare email obfuscation
    for el in fragment.select("[data-cfemail]"):
        el.replace_with(cf_decode(el["data-cfemail"]))
    for a in fragment.select('a[href*="/cdn-cgi/l/email-protection#"]'):
        addr = cf_decode(a["href"].split("#", 1)[1])
        a["href"] = "mailto:" + addr
    for el in fragment.find_all(True):
        if el.name == "a" and el.get("href"):
            el["href"] = rewrite_url(el["href"], base)
        if el.name == "img" and (el.get("alt") or "").strip().lower() == "null":
            el["alt"] = ""   # Plone's editor wrote the literal string "null" as alt text
        if el.name == "img" and el.get("src"):
            el["src"] = rewrite_url(el["src"], base, is_img=True)
            el["loading"] = "lazy"
        align = (el.get("style") or "")
        el.attrs = {k: v for k, v in el.attrs.items() if k in KEEP_ATTRS or k == "loading"}
        if el.name in ("p", "td", "th", "h2", "h3", "h4") and re.search(r"text-align:\s*center", align):
            el["class"] = "center"
        if el.name == "span" and not el.attrs:
            el.unwrap()
    for el in fragment.find_all(["p", "div"]):
        if not el.get_text(strip=True).replace("\xa0", "") and not el.find(["img", "iframe", "table"]):
            el.decompose()
    html = fragment.decode_contents() if hasattr(fragment, "decode_contents") else str(fragment)
    return re.sub(r"\n\s*\n+", "\n", html).strip()


# The original site's programmes (its top navigation). An item belongs to the programme whose
# folder it lives in, plus any programme named in its tags (news and events sit outside them).
PROGRAMMES = {"accessibility": "Accessibility", "a2k": "Access to Knowledge", "openness": "Openness",
              "internet-governance": "Internet Governance", "telecom": "Telecom", "raw": "RAW",
              "digital-natives": "Digital Natives"}
PROGRAMME_TAGS = {v.lower(): v for v in PROGRAMMES.values()} | {"researchers at work": "RAW"}


def categories(urlpath, tags):
    cats = [PROGRAMMES[s] for s in [urlpath.strip("/").split("/")[0]] if s in PROGRAMMES]
    cats += [PROGRAMME_TAGS[t.strip().lower()] for t in tags if t.strip().lower() in PROGRAMME_TAGS]
    return list(dict.fromkeys(cats))


def text(el):
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else None


def convert(item):
    path = item.get("html")
    if path and not os.path.exists(path):  # crawl dir was moved: look next to inventory.json
        path = f"{CRAWL}/html/{os.path.basename(path)}"
    if not path or not os.path.exists(path):
        stats["skipped"] += 1; return
    soup = BeautifulSoup(open(path).read(), "lxml")
    url = item["url"]; urlpath = urlparse(url).path.rstrip("/")
    title = text(soup.select_one("#parent-fieldname-title")) or item["title"]
    content = soup.select_one("div.content")
    if content is None:   # pages in another theme (e.g. /internet-institute/): use the text field
        text_field = soup.select_one('[id^="parent-fieldname-text"]')
        content = BeautifulSoup('<div class="content"><div class="keyResearch"></div></div>', "lxml").div
        intro = soup.select_one('[id^="parent-fieldname-description"]')
        if intro:
            p = soup.new_tag("p", attrs={"class": "sectionIntro"}); p.string = text(intro); content.insert(0, p)
        if text_field:
            content.select_one("div.keyResearch").append(text_field)
    fm = {
        "title": title,
        "date": parse_date(item.get("published")),
        "lastmod": parse_date(item.get("modified")),
        "authors": item.get("authors") or [],
        "tags": item.get("tags") or [],
        "description": text(content.select_one("p.sectionIntro, p.articleIntro")) or item.get("listing_description") or "",
        # keep the original path exactly (Hugo would lowercase it and turn spaces into dashes);
        # a slug ending in "." would be written as a file, so name index.html explicitly
        "url": unquote(urlpath) + ("/index.html" if urlpath.endswith(".") else "/"),
        "original_url": url,
        "type": TYPE_MAP.get(item["type"], "blog"),
        "program": urlpath.strip("/").split("/")[0],
        "categories": categories(urlpath, item.get("tags") or []),
    }
    lead = content.select_one("img.newsImage")
    if lead:
        orig = re.sub(r"/image_[a-z]+$", "/image", urljoin(url + "/", lead["src"]))
        fm["image"] = local_file(orig) or local_file(lead["src"]) or (missing.add(orig) or orig)
        cap = content.select_one('[id^="parent-fieldname-imageCaption"]')
        if cap and text(cap):
            fm["image_caption"] = text(cap)
    if item["type"] == "Event":
        body = content.select_one('div.content-core [id^="parent-fieldname-text"]') or content.select_one("div.content-core")
        ev = {}
        for k, sel in (("start", "abbr.dtstart"), ("end", "abbr.dtend")):
            el = soup.select_one(sel)
            if el: ev[k] = el.get("title")
        for det in soup.select(".eventDetails .eventDetail"):
            label = text(det.find("h3")); val = det.find("div")
            if not label or not val or label in ("When", "Add event to calendar"):
                continue
            if label == "Attendees":
                ev["attendees"] = [re.sub(r"^\d+\s+", "", text(s)) for s in val.find_all("span") if text(s)]
            elif label == "Contact Name":
                a = val.find("a"); ev["contact_name"] = text(val)
                if a and "email-protection#" in a.get("href", ""):
                    ev["contact_email"] = cf_decode(a["href"].split("#", 1)[1])
            elif label == "Web Visit":
                a = val.find("a"); ev["event_url"] = a["href"] if a else text(val)
            else:
                ev[label.lower().replace(" ", "_")] = text(val)
        fm["event"] = ev
        if body:
            for d in body.select(".eventDetails, .articleTools"):
                d.decompose()
    else:
        body = content.select_one("div.keyResearch") or content
        for d in body.select(".documentActions, #viewlet-below-content, .newsImageContainer, p.sectionIntro"):
            d.decompose()
    html = clean(body, url) if body else ""
    out = f"{SITE}/content{unquote(urlpath)}/index.html"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fm = {k: v for k, v in fm.items() if v not in (None, "", [])}
    with open(out, "w") as f:
        f.write("---\n" + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False) + "---\n" + html + "\n")
    stats["items"] += 1


errors = []
for it in items:
    try:
        convert(it)
    except Exception as e:   # keep going; list failures in the report
        errors.append({"url": it["url"], "error": repr(e)})
json.dump({"stats": stats, "errors": errors, "missing_files": sorted(missing)},
          open(f"{os.path.dirname(SITE.rstrip('/'))}/convert_report.json", "w"), indent=1)
print(stats, "missing:", len(missing), "errors:", len(errors))
