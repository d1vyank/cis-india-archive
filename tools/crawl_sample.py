"""Stage 1: fetch a sample of blog entries and events from cis-india.org.

Uses Plone's @@search listings (10 per page) for metadata, then fetches each
item page and every attachment hosted on cis-india.org that it links to.
Writes: OUT/listing.json, OUT/html/<slug>.html, OUT/files/<path>, OUT/inventory.json
"""
import json, os, re, sys, time, hashlib
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse, unquote
import requests
from bs4 import BeautifulSoup

BASE = "https://cis-india.org"
OUT = sys.argv[1]
UA = "cis-india-archive-poc/0.1 (static archive of cis-india.org)"
S = requests.Session(); S.headers["User-Agent"] = UA
WORKERS = 1          # one request at a time
DELAY = 3.0          # seconds to wait after each response before the next request
_last = [0.0]

BLOG_OFFSETS = [0, 400, 800, 1200, 1600, 2000, 2400, 2800]   # 80 blog entries
EVENT_OFFSETS = [0, 220]                                      # 20 events
FILE_EXT = re.compile(r"\.(pdf|docx?|odt|pptx?|odp|xlsx?|ods|csv|zip|mp3|mp4|jpe?g|png|gif|svg|txt|rtf|epub)$", re.I)


def get(url, **kw):
    for attempt in range(4):
        try:
            wait = _last[0] + DELAY - time.time()
            if wait > 0:
                time.sleep(wait)
            try:
                r = S.get(url, timeout=240, **kw)
            finally:
                _last[0] = time.time()
            if r.status_code == 429:
                time.sleep(int(r.headers.get("Retry-After", 120)))
                raise requests.RequestException("HTTP 429")
            if r.status_code >= 500:
                raise requests.RequestException(f"HTTP {r.status_code}")
            return r
        except requests.RequestException as e:
            if attempt == 3:
                print("FAIL", url, e, flush=True); return None
            time.sleep(30 * 2 ** attempt)


def parse_listing(html, ptype):
    soup = BeautifulSoup(html, "lxml")
    items = []
    for dt in soup.select("dl.searchResults > dt"):
        a = dt.find("a"); dd = dt.find_next_sibling("dd")
        authors = [x.get_text(strip=True) for x in dd.select(".documentAuthor a")] if dd else []
        pub = dd.select_one(".documentPublished") if dd else None
        mod = dd.select_one(".documentModified") if dd else None
        desc = dd.find("div", recursive=True) if dd else None
        clean = lambda el: re.sub(r"\s+", " ", el.get_text(" ", strip=True)).replace("— published", "").replace("— last modified", "").strip() if el else None
        items.append({
            "url": a["href"], "title": a.get_text(strip=True), "type": ptype,
            "authors": authors, "published": clean(pub), "modified": clean(mod),
            "tags": [t.get_text(strip=True) for t in dd.select("a.link-category")] if dd else [],
            "listing_description": desc.get_text(" ", strip=True) if desc else "",
        })
    return items


def local_path(url):
    p = unquote(urlparse(url).path).strip("/")
    return p


SCALE_RE = re.compile(r"/(image_[a-z]+|image/image_view_fullscreen|@@images/.*|at_download/file|view)$")


def normalise(url):
    """Fetch originals, not Plone scales: foo.jpg/image_preview -> foo.jpg, post/image_mini -> post/image."""
    stripped = SCALE_RE.sub("", url)
    if stripped != url and FILE_EXT.search(urlparse(stripped).path):
        return stripped
    return re.sub(r"/image_[a-z]+$", "/image", url)


def is_internal_file(url):
    u = urlparse(url)
    if u.netloc and u.netloc not in ("cis-india.org", "www.cis-india.org"):
        return False
    p = u.path
    return bool(FILE_EXT.search(p) or "/at_download/" in p or "@@images" in p
                or re.search(r"/image(_[a-z]+)?$", p))


def main():
    os.makedirs(f"{OUT}/html", exist_ok=True); os.makedirs(f"{OUT}/files", exist_ok=True)
    jobs = [("Blog Entry", o) for o in BLOG_OFFSETS] + [("Event", o) for o in EVENT_OFFSETS]

    def fetch_listing(job):
        ptype, off = job
        r = get(f"{BASE}/@@search", params={"portal_type:list": ptype, "sort_on": "Date", "b_start:int": off})
        if r is None or r.status_code != 200:
            print("LISTING FAIL", ptype, off, r and r.status_code, flush=True); return []
        return parse_listing(r.text, ptype)

    with ThreadPoolExecutor(WORKERS) as ex:
        items = json.load(open(f"{OUT}/listing.json")) if os.path.exists(f"{OUT}/listing.json") else [i for batch in ex.map(fetch_listing, jobs) for i in batch]
    json.dump(items, open(f"{OUT}/listing.json", "w"), indent=1)
    print(f"listing: {len(items)} items", flush=True)

    def fetch_item(item):
        key = hashlib.sha1(item["url"].encode()).hexdigest()[:10]
        path = f"{OUT}/html/{key}.html"
        t = time.time()
        if not os.path.exists(path):
            r = get(item["url"])
            if r is None or r.status_code != 200:
                item["error"] = r.status_code if r is not None else "fail"; return item
            open(path, "w").write(r.text)
        item["html"] = path; item["fetch_secs"] = round(time.time() - t, 1)
        soup = BeautifulSoup(open(path).read(), "lxml")
        main_el = soup.select_one("div.content") or soup
        links = set()
        for el in main_el.find_all(["a", "img"]):
            ref = el.get("href") or el.get("src")
            if not ref or ref.startswith(("mailto:", "#", "javascript:")):
                continue
            absu = urljoin(item["url"] + "/", ref).split("#")[0]
            internal = urlparse(absu).netloc in ("cis-india.org", "www.cis-india.org")
            if absu.startswith("http") and (is_internal_file(absu) or (el.name == "img" and internal)):
                links.add(normalise(absu))
        item["attachments"] = sorted(links)
        return item

    with ThreadPoolExecutor(WORKERS) as ex:
        items = list(ex.map(fetch_item, items))
    print("pages fetched", flush=True)

    all_files = sorted({u for i in items for u in i.get("attachments", [])})
    print(f"attachments to fetch: {len(all_files)}", flush=True)

    def fetch_file(u):
        dest = f"{OUT}/files/{local_path(u)}"
        if os.path.isdir(dest):
            dest = dest + "/__index"
        if os.path.exists(dest):
            return u, os.path.getsize(dest), "cached", None
        r = get(u, stream=True)
        if r is None or r.status_code != 200:
            return u, 0, r.status_code if r is not None else "fail", None
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
        except OSError:
            return u, 0, "path-conflict", None
        return u, os.path.getsize(dest), r.status_code, r.headers.get("content-type")

    with ThreadPoolExecutor(WORKERS) as ex:
        res = list(ex.map(fetch_file, all_files))
    files = {u: {"bytes": b, "status": s, "content_type": ct} for u, b, s, ct in res}
    json.dump({"items": items, "files": files}, open(f"{OUT}/inventory.json", "w"), indent=1)
    print("done", flush=True)


if __name__ == "__main__":
    main()
