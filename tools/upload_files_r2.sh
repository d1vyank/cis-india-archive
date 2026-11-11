#!/bin/sh
# Upload the attachments (ARCHIVE/static/files) to a Cloudflare R2 bucket, keeping their paths
# under files/ so that FILES_BASE/files/<path> serves them. Re-running only sends changed files.
# Usage: tools/upload_files_r2.sh ARCHIVE_DIR BUCKET
# Needs rclone (https://rclone.org/install/) with a remote named "r2" set up for R2:
#   rclone config create r2 s3 provider=Cloudflare access_key_id=KEY secret_access_key=SECRET \
#     endpoint=https://ACCOUNT_ID.r2.cloudflarestorage.com acl=private
set -e
[ $# -eq 2 ] || { echo "usage: $0 ARCHIVE_DIR BUCKET" >&2; exit 2; }
SRC="$1/static/files"
[ -d "$SRC" ] || { echo "no $SRC: run tools/convert.py first" >&2; exit 1; }
echo "uploading $(find "$SRC" -type f | wc -l) files ($(du -sh "$SRC" | cut -f1)) to r2:$2/files"
rclone copy "$SRC" "r2:$2/files" --s3-no-check-bucket --transfers 8 --checksum --progress
