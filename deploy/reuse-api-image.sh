#!/usr/bin/env bash
# Compatibility entry point for the persistent dependency-image workflow.
set -Eeuo pipefail

if [[ $# -gt 1 ]]; then
  printf 'Usage: bash deploy/reuse-api-image.sh [local-api-image]\n' >&2
  exit 2
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$script_dir/build-production.py" --api-only --require-reuse \
  --bootstrap-image "${1:-kpl-api:latest}"
