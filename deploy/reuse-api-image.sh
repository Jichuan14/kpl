#!/usr/bin/env bash
# Recover a code-only API deployment using packages in an existing local image.
# This is a recovery path, not a replacement for dependency/base-image updates.
set -Eeuo pipefail

if [[ $# -gt 1 ]]; then
  printf 'Usage: bash deploy/reuse-api-image.sh [local-api-image]\n' >&2
  exit 2
fi

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
api_image="${1:-kpl-api:latest}"
check_dir="$(mktemp -d "${TMPDIR:-/tmp}/kpl-api-reuse.XXXXXX")"
trap 'rm -rf "$check_dir"' EXIT

# These fingerprints deliberately limit recovery to the reviewed build recipe.
# If either changes, use the regular build instead of silently inheriting it.
recipe_sha=bfcc1cb798a6cc8a53e635cc3249eadb3496319bd0b8e0b0c88be4e8e990924e
ignore_sha=09d987912cdf0d74f05bd3306ca54997c724d58ed1fe15d26b3ccb03a0a6027e

cp backend/requirements.txt "$check_dir/requirements.txt"
cp backend/requirements-training.txt "$check_dir/requirements-training.txt"
cp backend/Dockerfile "$check_dir/Dockerfile"
cp .dockerignore "$check_dir/dockerignore"
base_id="$(docker image inspect "$api_image" --format '{{.Id}}')"
docker image inspect "$base_id" --format '{{json .}}' > "$check_dir/image.json"

printf 'Checking requirements and runtime configuration in %s...\n' "$api_image"
docker run --rm --pull=never --network=none --read-only --no-healthcheck \
  --entrypoint python \
  --mount "type=bind,src=$check_dir,dst=/check,readonly" \
  -i "$base_id" - "$recipe_sha" "$ignore_sha" /check /app <<'PY'
import hashlib
import json
import sys
from pathlib import Path

recipe_sha, ignore_sha, check_path, app_path = sys.argv[1:]
check = Path(check_path)
app = Path(app_path)

def require(condition, message):
    if not condition:
        raise SystemExit("Cannot reuse this image: " + message)

require(hashlib.sha256((check / "Dockerfile").read_bytes()).hexdigest() == recipe_sha,
        "the Dockerfile changed; use a regular build.")
require(hashlib.sha256((check / "dockerignore").read_bytes()).hexdigest() == ignore_sha,
        "the Docker ignore rules changed; use a regular build.")
for name in ("requirements.txt", "requirements-training.txt"):
    require((check / name).read_bytes() == (app / "backend" / name).read_bytes(),
            name + " differs from the installed image; use a regular build.")

config = json.loads((check / "image.json").read_text())["Config"]
require(config.get("WorkingDir") == "/app/backend", "unexpected working directory.")
require(config.get("Cmd") == ["uvicorn", "app.main:app", "--host", "0.0.0.0",
        "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips=*"],
        "unexpected startup command.")
require(not config.get("Entrypoint") and not config.get("OnBuild")
        and not config.get("Volumes") and config.get("User", "") in ("", "root", "0"),
        "unexpected inherited image configuration.")
require({"PYTHONDONTWRITEBYTECODE=1", "PYTHONUNBUFFERED=1"}.issubset(config.get("Env") or []),
        "unexpected Python environment.")
require((config.get("Healthcheck") or {}).get("Test") == ["CMD-SHELL",
        "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)\""],
        "unexpected health check.")
print("Requirements and runtime configuration match. No package installation is needed.")
PY

# Give the immutable source image a separate local tag before replacing latest.
# Keep this tag for rollback; no live containers are changed by this script.
base_hex="${base_id#sha256:}"
base_tag="kpl-api:reuse-base-${base_hex:0:12}"
docker image tag "$base_id" "$base_tag"

cat > "$check_dir/reuse.Dockerfile" <<EOF
FROM $base_tag
# Remove old source first so deleted files cannot survive this recovery build.
RUN rm -rf /app/backend /app/analysis /app/knowledge
COPY backend /app/backend
COPY analysis /app/analysis
COPY knowledge/sources/official/herolist.json /app/knowledge/sources/official/herolist.json
WORKDIR /app/backend
EOF

# Use the Engine builder so the local source image is available. No external
# Dockerfile syntax image is requested and RUN instructions have no network.
docker buildx build --builder default --load --pull=false --network=none \
  --progress=plain -f "$check_dir/reuse.Dockerfile" -t "$api_image" .

printf 'Built %s using existing packages. Rollback image: %s\n' "$api_image" "$base_tag"
printf 'Start it with: docker compose -f docker-compose.production.yml up -d --no-build --wait\n'
