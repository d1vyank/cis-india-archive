"""Make the embedded media in converted pages play on the archive.

The original pages embed video and audio three ways, and none of them played on the archive:
  * <iframe src="http://..."> - the archive is served over HTTPS, so browsers block
    http:// frames as mixed content. Rewritten to https:// (every host still in use supports it).
  * <object>/<embed> Flash players (YouTube /v/, SoundCloud, Vimeo) - browsers dropped Flash
    in 2021. Replaced with the same service's current iframe player.
  * Players from services that have shut down (blip.tv, Storify) and other Flash with no
    successor - replaced by a short note, with a link where there is somewhere to go.
Iframes also get allowfullscreen, lazy loading and a title; video players get class="video"
so the theme can size them to the column.

Used by convert.py on every page. Run it directly to re-apply to already converted content:
    python3 tools/embeds.py archive/content
"""
import re, sys
from urllib.parse import urlparse, parse_qs, unquote

DEAD_HOSTS = {"blip.tv": "blip.tv", "storify.com": "Storify"}
VIDEO_HOSTS = ("youtube.com", "youtube-nocookie.com", "player.vimeo.com", "archive.org",
               "content.jwplatform.com", "vidshare.indianexpress.com")
YT_ID = re.compile(r"(?:youtube(?:-nocookie)?\.com/(?:v|embed)/|youtu\.be/)([\w-]{11})")


def _host(url):
    h = urlparse(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def _dead(url):
    h = _host(url)
    return next((name for d, name in DEAD_HOSTS.items() if h == d or h.endswith("." + d)), None)


def _https(url):
    url = url.strip()
    if url.startswith("//"):
        return "https:" + url
    return re.sub(r"^http://", "https://", url, flags=re.I)


def _media_id(url):
    """blip.tv embeds come in pairs for one video (iframe /play/ID and Flash api.swf#ID)."""
    u = urlparse(url)
    return u.fragment or re.sub(r"\.html$", "", u.path.rstrip("/").rsplit("/", 1)[-1])


def _gone(service):
    return f"[Embedded media no longer available: {service} has shut down.]"


def _note(soup, text, href=None):
    p = soup.new_tag("span", attrs={"class": "embed-gone"})   # may sit inside a <p>
    p.append(text)
    if href:
        p.append(" ")
        a = soup.new_tag("a", href=href)
        a.string = "Open the original link."
        p.append(a)
    return p


def _flash_successor(src):
    """Current iframe URL for an old Flash player URL, or None."""
    m = YT_ID.search(src)
    if m:
        start = parse_qs(urlparse(src.replace("&amp;", "&")).query).get("start")
        return f"https://www.youtube.com/embed/{m.group(1)}" + (f"?start={start[0]}" if start else "")
    q = parse_qs(urlparse(src).query)
    if "soundcloud.com" in _host(src) and q.get("url"):
        return "https://w.soundcloud.com/player/?url=" + _https(unquote(q["url"][0]))
    if "vimeo.com" in _host(src) and q.get("clip_id"):
        return f"https://player.vimeo.com/video/{q['clip_id'][0]}"
    return None


def _iframe(soup, src, width=None, height=None, title=None):
    f = soup.new_tag("iframe", src=src)
    if width: f["width"] = width
    if height: f["height"] = height
    return _finish(f, title)


def _finish(f, title=None):
    f.clear()   # fallback text inside an iframe is never shown (and some is mangled by escaping)
    f["allowfullscreen"] = ""
    f["loading"] = "lazy"
    if not f.get("title"):
        f["title"] = title or ("Embedded video" if _is_video(f["src"]) else "Embedded content")
    if _is_video(f["src"]):
        f["class"] = "video"
    return f


def _is_video(src):
    h = _host(src)
    return any(h == v or h.endswith("." + v) for v in VIDEO_HOSTS)


def fix_embeds(fragment, soup=None):
    """Rewrite iframes, objects and embeds inside a BeautifulSoup fragment, in place.
    Returns a dict of counts for the conversion report."""
    if soup is None:   # new_tag lives on the BeautifulSoup root, not on a Tag
        soup = fragment
        while soup.parent is not None:
            soup = soup.parent
    n = {"iframe_https": 0, "flash_replaced": 0, "dead_noted": 0}
    noted = set()

    def gone(el, src, dead):
        key = (dead, _media_id(src))
        if key in noted:   # second embed of the same video: one note is enough
            el.decompose(); return
        noted.add(key); n["dead_noted"] += 1
        el.replace_with(_note(soup, _gone(dead)))

    for f in fragment.find_all("iframe"):
        src = f.get("src") or ""
        if not src or f.get("title") == "3rd party ad content":
            f.decompose(); continue
        dead = _dead(src)
        if dead:
            gone(f, src, dead); continue
        new = _https(src)
        n["iframe_https"] += new != src
        f["src"] = new
        _finish(f)
    # Links to the old Flash player URL (youtube.com/v/ID) no longer open; point them at the video.
    for a in fragment.find_all("a", href=re.compile(r"youtube\.com/v/")):
        m = YT_ID.search(a["href"])
        if m:
            a["href"] = f"https://www.youtube.com/watch?v={m.group(1)}"
    # <object> wraps an <embed> (or <param name=movie>); handle the outermost element once.
    for el in fragment.find_all(["object", "embed"]):
        if el.decomposed or el.find_parent(["object", "embed"]):
            continue
        inner = el if el.name == "embed" else el.find("embed")
        src = (inner.get("src") if inner else None) or el.get("data") or ""
        if not src:
            p = el.find("param", attrs={"name": re.compile("^(movie|src)$", re.I)})
            src = (p.get("value") if p else "") or ""
        width = (inner or el).get("width") or el.get("width")
        height = (inner or el).get("height") or el.get("height")
        new = _flash_successor(src)
        if new:
            el.replace_with(_iframe(soup, new, width, height)); n["flash_replaced"] += 1; continue
        dead = _dead(src)
        if dead:
            gone(el, src, dead); continue
        # Some other Flash player: point to the media it loaded where we can tell.
        q = parse_qs(urlparse(src).query)
        target = next((unquote(v[0]) for k in ("feed", "url") for v in [q.get(k)] if v), None)
        if not target and src and not re.search(r"\.swf($|[?#])", src, re.I):
            target = src
        el.replace_with(_note(soup, "[Embedded Flash media, which browsers can no longer play.]",
                              _https(target) if target and target.startswith(("http", "//")) else None))
        n["dead_noted"] += 1
    return n


if __name__ == "__main__":
    import os
    from bs4 import BeautifulSoup
    total, changed = {}, 0
    for root, _, files in os.walk(sys.argv[1]):
        for name in files:
            if not name.endswith(".html"):
                continue
            path = os.path.join(root, name)
            text = open(path, encoding="utf-8").read()
            if not re.search(r"<(iframe|object|embed)\b|youtube\.com/v/", text, re.I):
                continue
            m = re.match(r"(---\n.*?\n---\n)(.*)", text, re.S)
            head, body = (m.group(1), m.group(2)) if m else ("", text)
            frag = BeautifulSoup(body, "html.parser")
            for k, v in fix_embeds(frag).items():
                total[k] = total.get(k, 0) + v
            for el in frag.find_all(["p", "div"]):   # as convert.py's clean() does afterwards
                if not el.get_text(strip=True).replace("\xa0", "") and not el.find(["img", "iframe", "table"]):
                    el.decompose()
            out = head + frag.decode()
            if out != text:
                open(path, "w", encoding="utf-8").write(out); changed += 1
    print(f"pages changed: {changed}", total)
