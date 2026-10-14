#!/usr/bin/env bash
# Back up the database and the stored result files, keeping the last 7 days.
#
#   ./deploy/free/backup.sh                 (also runs daily from cron)
#   BACKUP_DIR=/mnt/backups ./deploy/free/backup.sh
#
# Backups live on the same server, so they protect against mistakes, not
# against losing the server. Copy them somewhere else now and then, keeping
# them encrypted: they contain the same personal data as the live site.
#
# Restore (best done while nobody is using the site):
#   ./deploy/free/polygraph.sh exec -T postgres sh -c \
#     'pg_restore --clean --if-exists -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < backups/db-<stamp>.dump
#   ./deploy/free/polygraph.sh stop s3
#   docker run --rm -i -v polygraph_s3data:/data alpine:3.22 sh -c 'rm -rf /data/* && tar -C /data -xz' \
#     < backups/files-<stamp>.tar.gz
#   ./deploy/free/polygraph.sh up -d
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
dest="${BACKUP_DIR:-$root/backups}"
keep_days=7
stamp="$(date -u +%Y%m%dT%H%M%SZ)"

umask 077
mkdir -p "$dest"

"$here/polygraph.sh" exec -T postgres \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' >"$dest/db-$stamp.dump.part"
mv "$dest/db-$stamp.dump.part" "$dest/db-$stamp.dump"

# A copy of the S3 server's volume taken while it runs. Uploads that land
# mid-copy may be missing from it; the database dump is the part that matters.
docker run --rm -v polygraph_s3data:/data:ro alpine:3.22 tar -C /data -cz . >"$dest/files-$stamp.tar.gz.part"
mv "$dest/files-$stamp.tar.gz.part" "$dest/files-$stamp.tar.gz"

find "$dest" -maxdepth 1 -type f \( -name 'db-*.dump' -o -name 'files-*.tar.gz' -o -name '*.part' \) \
  -mtime +"$keep_days" -delete

echo "Backed up to $dest ($stamp)."
