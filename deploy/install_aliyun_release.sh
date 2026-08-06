#!/usr/bin/env bash

set -Eeuo pipefail

usage() {
  printf 'Usage: %s /absolute/path/to/live/kpl\n' "$0" >&2
  printf 'Extract the release ZIP outside the live directory, then run this script.\n' >&2
  exit 2
}

if [[ $# -ne 1 || "$1" != /* ]]; then
  usage
fi

for command_name in docker rsync curl; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    printf 'Required command not found: %s\n' "$command_name" >&2
    exit 1
  fi
done

release_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target_root="$(cd "$1" && pwd)"

if [[ "$release_root" == "$target_root" ]]; then
  printf 'The release must be extracted outside the live project directory.\n' >&2
  exit 1
fi

for required_path in \
  "$release_root/backend/app/main.py" \
  "$release_root/frontend/package.json" \
  "$release_root/analysis" \
  "$release_root/docker-compose.production.yml" \
  "$target_root/backend" \
  "$target_root/docker-compose.production.yml" \
  "$target_root/.env.production" \
  "$target_root/deploy/.htpasswd"; do
  if [[ ! -e "$required_path" ]]; then
    printf 'Required path is missing: %s\n' "$required_path" >&2
    exit 1
  fi
done

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_parent="$(dirname "$target_root")/kpl-release-backups"
backup_root="$backup_parent/$timestamp"
mkdir -p "$backup_root"

printf 'Stopping the live containers...\n'
docker compose -f "$target_root/docker-compose.production.yml" stop api web

printf 'Backing up the existing backend, analysis data, and server secrets to %s\n' "$backup_root"
cp -a "$target_root/backend" "$backup_root/backend"
mkdir -p "$backup_root/analysis" "$backup_root/deploy"
for analysis_dir in exports outputs published; do
  if [[ -d "$target_root/analysis/$analysis_dir" ]]; then
    cp -a "$target_root/analysis/$analysis_dir" "$backup_root/analysis/$analysis_dir"
  fi
done
cp -a "$target_root/.env.production" "$backup_root/.env.production"
cp -a "$target_root/deploy/.htpasswd" "$backup_root/deploy/.htpasswd"
if [[ -f "$target_root/backend/.env" ]]; then
  cp -a "$target_root/backend/.env" "$backup_root/backend/.env"
fi

printf 'Replacing application directories from the release...\n'
# --delete is deliberate: removed files in these directories must not survive
# the deployment. In particular, backend/ becomes an exact release copy.
for project_dir in backend frontend analysis agent; do
  mkdir -p "$target_root/$project_dir"
  if [[ "$project_dir" == "backend" ]]; then
    # An existing backend/.env may contain a runtime secret. It is never part
    # of the release and must remain on the server even with --delete.
    rsync -a --delete --exclude='.env' "$release_root/$project_dir/" "$target_root/$project_dir/"
  else
    rsync -a --delete "$release_root/$project_dir/" "$target_root/$project_dir/"
  fi
done

# Keep the server-specific password file while updating deploy documentation
# and scripts.
rsync -a --exclude='.htpasswd' "$release_root/deploy/" "$target_root/deploy/"

for root_file in \
  README.md \
  CALCULATION_METHODOLOGY.md \
  docker-compose.production.yml \
  .env.production.example; do
  if [[ -f "$release_root/$root_file" ]]; then
    rsync -a "$release_root/$root_file" "$target_root/$root_file"
  fi
done

printf 'Building and starting the updated application...\n'
docker compose -f "$target_root/docker-compose.production.yml" up -d --build

printf 'Waiting for the local health endpoint...\n'
healthy=0
for _ in {1..30}; do
  if curl --fail --silent --show-error http://127.0.0.1/health >/dev/null; then
    healthy=1
    break
  fi
  sleep 2
done

if [[ "$healthy" -ne 1 ]]; then
  printf 'Deployment finished, but the health check failed. Backup: %s\n' "$backup_root" >&2
  docker compose -f "$target_root/docker-compose.production.yml" ps >&2
  exit 1
fi

printf 'Deployment succeeded. Backup retained at: %s\n' "$backup_root"
