#!/usr/bin/env python3
"""Upload the attachments (ARCHIVE/static/files) to an Internet Archive item, keeping their paths.

Each file goes to files/<path> in the item, so https://archive.org/download/<item>/files/<path>
mirrors /files/<path> on the site. tools/build.sh points the site's fallback at that URL: when the
Pages Function that serves /files/* from R2 runs out of its free daily quota, /files/* redirects
there instead (see README, "Internet Archive fallback").

Re-running is safe: files already in the item with the same MD5 are skipped, so an interrupted
upload resumes where it stopped. Derivatives (OCR, thumbnails) are not generated.

Usage:
  pip install internetarchive
  ia configure                       # or set IA_ACCESS_KEY / IA_SECRET_KEY (archive.org/account/s3.php)
  python3 tools/upload_files_ia.py ARCHIVE_DIR [--item cis-india-org-attachments] [--dry-run]
  python3 tools/upload_files_ia.py ARCHIVE_DIR --verify   # only compare, upload nothing
"""
import argparse
import hashlib
import os
import sys
import time

DEFAULT_ITEM = "cis-india-org-attachments"
METADATA = {
    "title": "Centre for Internet and Society (cis-india.org): blog, event and news attachments",
    "mediatype": "data",
    "collection": "opensource_media",
    "creator": "Centre for Internet and Society, India",
    "subject": ["cis-india.org", "Centre for Internet and Society", "internet governance",
                "access to knowledge", "openness", "India"],
    "description": (
        "PDFs, images and other files attached to blog posts, events and news items published by the "
        "Centre for Internet and Society, India, at cis-india.org (2008 to 2025), taken from the "
        "2026-11-07 server dump. Paths under files/ match the original site's URLs. "
        "Used as the fallback file host of the static archive at https://cis-india-archive.pages.dev/ "
        "(source: https://github.com/d1vyank/cis-india-archive)."
    ),
    "language": "eng",
}


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def local_files(src):
    out = {}
    for root, _, names in os.walk(src):
        for n in names:
            p = os.path.join(root, n)
            out["files/" + os.path.relpath(p, src).replace(os.sep, "/")] = p
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archive_dir", help="output of tools/convert.py (holds static/files/)")
    ap.add_argument("--item", default=DEFAULT_ITEM, help=f"archive.org identifier (default {DEFAULT_ITEM})")
    ap.add_argument("--licenseurl", default="https://creativecommons.org/licenses/by/3.0/",
                    help="licence of the content (default CC BY 3.0, as on cis-india.org)")
    ap.add_argument("--batch", type=int, default=25, help="files per upload request batch")
    ap.add_argument("--dry-run", action="store_true", help="list what would be uploaded")
    ap.add_argument("--verify", action="store_true", help="compare local and remote files only")
    a = ap.parse_args()

    import internetarchive as ia  # pip install internetarchive

    src = os.path.join(a.archive_dir, "static", "files")
    if not os.path.isdir(src):
        sys.exit(f"no {src}: run tools/convert.py first")
    local = local_files(src)
    total = sum(os.path.getsize(p) for p in local.values())
    print(f"{len(local)} local files, {total / 1e9:.2f} GB")

    config = {}
    if os.environ.get("IA_ACCESS_KEY") and os.environ.get("IA_SECRET_KEY"):
        config = {"s3": {"access": os.environ["IA_ACCESS_KEY"], "secret": os.environ["IA_SECRET_KEY"]}}
    session = ia.get_session(config=config)
    item = session.get_item(a.item)
    remote = {f["name"]: f for f in (item.files or [])}
    print(f"item {a.item}: {'exists' if item.exists else 'new'}, {len(remote)} files")

    todo, same = [], 0
    for name, path in sorted(local.items()):
        r = remote.get(name)
        if r and int(r.get("size", -1)) == os.path.getsize(path) and r.get("md5") == md5(path):
            same += 1
        else:
            todo.append(name)
    print(f"{same} already uploaded, {len(todo)} to upload "
          f"({sum(os.path.getsize(local[n]) for n in todo) / 1e9:.2f} GB)")
    if a.verify or a.dry_run:
        for n in todo[:50]:
            print("  missing/different:", n)
        if len(todo) > 50:
            print(f"  ... and {len(todo) - 50} more")
        sys.exit(1 if (a.verify and todo) else 0)
    if not session.access_key:
        sys.exit("no archive.org keys: run `ia configure` or set IA_ACCESS_KEY / IA_SECRET_KEY")

    metadata = dict(METADATA, licenseurl=a.licenseurl)
    failed, created = [], item.exists
    for i in range(0, len(todo), a.batch):
        names = todo[i:i + a.batch]
        print(f"[{i + len(names)}/{len(todo)}] {names[0]} ...", flush=True)
        try:
            rs = item.upload({n: local[n] for n in names},
                             metadata=None if created else metadata,  # sets up a new item
                             queue_derive=False, verify=True,
                             retries=10, retries_sleep=30)
            bad = [n for n, r in zip(names, rs) if not getattr(r, "ok", True)]
            failed += bad
            created = created or len(bad) < len(names)
        except Exception as e:  # keep going; a re-run picks up whatever failed
            print(f"  batch failed: {e}", file=sys.stderr)
            failed += names
            time.sleep(60)
    print(f"done; {len(failed)} failed" + (" (re-run to retry)" if failed else ""))
    for n in failed:
        print("  failed:", n)
    print(f"Files appear at https://archive.org/download/{a.item}/files/<path> once archive.org "
          "has processed the upload tasks (minutes to hours). Check with --verify.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
