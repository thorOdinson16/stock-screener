#!/usr/bin/env bash
#
# backup_results.sh <destination-dir> — archive the research evidence and models.
#
# ml/evaluation/results/ (frozen protocols, prediction checkpoints, the paper
# trials, collected filings, ~1.5 GB) and ml/models/ are gitignored, so they exist
# only on this disk. The frozen trials cite file hashes that nobody can re-derive
# if the directory is lost. This writes a timestamped .tar.gz plus a sha256 file;
# it only reads the repo and never modifies it.
#
# Restore: tar -xzf <archive> -C <repo-root>

source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

dest="${1:-}"
if [ -z "$dest" ]; then
  fail "usage: scripts/backup_results.sh <destination-dir>"
  exit 2
fi
mkdir -p "$dest"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$dest/screening-results-$stamp.tar.gz"
paths=()
for p in ml/evaluation/results ml/models config/airflow.env; do
  [ -e "$REPO_ROOT/$p" ] && [ "$p" != config/airflow.env ] && paths+=("$p")
done
if [ "${#paths[@]}" -eq 0 ]; then
  fail "Nothing to back up (ml/evaluation/results and ml/models are absent)"
  exit 1
fi

log "Archiving ${paths[*]} -> $archive"
tar -czf "$archive" -C "$REPO_ROOT" "${paths[@]}"
( cd "$dest" && sha256sum "$(basename "$archive")" > "$(basename "$archive").sha256" )
ok "Wrote $archive ($(du -h "$archive" | cut -f1)) and its .sha256"
