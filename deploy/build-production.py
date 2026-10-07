#!/usr/bin/env python3
"""Build production images using a persistent, content-addressed dependency image."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_MARKER = b"FROM ${DEPENDENCY_IMAGE} AS runtime\n"
DEPENDENCY_LABEL = "io.draft-atlas.dependencies.key"

# One-time migration from the audited pre-split API image. Only /usr/local
# (Python and pip-installed packages) is copied, never old application layers.
# Recipe changes deliberately disable this migration rather than guessing
# whether an older environment implements new package/system installation steps.
MIGRATION_RECIPE = "a3837cb4e0cade7e774b958992439fea97245b12a74e70cccf60db5930942bcf"
MIGRATION_BASE_LAYERS = [
    "sha256:15f1c8eb1ab18eae260c7fb6296434cbe1ff0e1e30fd814ae92a80dd54005e62",
    "sha256:e1fa2746ad6d51e7de9171dfcbb1fe93d01d51c5d8d3623e0c3b223076dc5d23",
    "sha256:b361fa3b97940ee89a1aab8fde1027e47ab2c2e700ca2f8d5988ad2c64386e5c",
    "sha256:dca2a125b4903477bcb42084172cfc98865299810620ffd617bf6e12dd83d89f",
]
MIGRATION_CHECK = """
import hashlib
from pathlib import Path
import subprocess
import sys
for name, expected in zip(('requirements.txt', 'requirements-training.txt'), sys.argv[1:]):
    actual = hashlib.sha256((Path('/app/backend') / name).read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(name + ' differs from the existing API image.')
subprocess.run([sys.executable, '-m', 'pip', 'check'], check=True)
print('Existing API packages match; migrating without pip install.')
"""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def dependency_spec(root, platform):
    dockerfile = (root / "backend/Dockerfile").read_bytes()
    if dockerfile.count(RUNTIME_MARKER) != 1:
        raise RuntimeError("Cannot locate the dependency stage in backend/Dockerfile.")
    recipe = dockerfile.split(RUNTIME_MARKER)[0]
    base = re.search(rb"^FROM (\S+) AS dependencies$", recipe, re.MULTILINE)
    if not base:
        raise RuntimeError("Cannot identify the dependency stage's Python base image.")
    inputs = {
        "schema": 1,
        "platform": platform,
        "recipe": digest(recipe),
        "api": digest((root / "backend/requirements.txt").read_bytes()),
        "training": digest((root / "backend/requirements-training.txt").read_bytes()),
    }
    return {
        **inputs,
        "key": digest(json.dumps(inputs, sort_keys=True).encode()),
        "base": base.group(1).decode(),
    }


def command(args, *, capture=False, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=capture, **kwargs)


def inspect_image(reference):
    result = subprocess.run(
        ["docker", "image", "inspect", reference], text=True, capture_output=True
    )
    if result.returncode:
        return None
    return json.loads(result.stdout)[0]


def migrate_existing(root, spec, image, destination):
    if spec["recipe"] != MIGRATION_RECIPE or spec["platform"] != "linux/amd64":
        print("Existing-image migration does not cover this dependency recipe/platform.", flush=True)
        return False
    info = inspect_image(image)
    if info is None:
        print(f"No local {image} image is available for migration.", flush=True)
        return False
    config = info.get("Config") or {}
    if (info.get("RootFS") or {}).get("Layers", [])[:4] != MIGRATION_BASE_LAYERS:
        print("Existing API uses a different Python base; its packages will not be copied.", flush=True)
        return False
    if info.get("Architecture") != "amd64" or info.get("Os") != "linux":
        return False
    if config.get("OnBuild") or config.get("User", "") not in ("", "root", "0"):
        print("Existing API has incompatible image configuration.", flush=True)
        return False
    # Use the immutable image ID, not a latest tag that another build can replace.
    try:
        command([
            "docker", "run", "--rm", "--pull=never", "--network=none", "--read-only",
            "--no-healthcheck", "--entrypoint", "python", "-i", info["Id"], "-",
            spec["api"], spec["training"],
        ], input=MIGRATION_CHECK)
    except subprocess.CalledProcessError:
        print("Existing-image package checks failed; dependency installation is required.", flush=True)
        return False

    source = "kpl-api-deps:seed-" + info["Id"].split(":", 1)[-1][:12]
    command(["docker", "image", "tag", info["Id"], source])
    try:
        with tempfile.TemporaryDirectory(prefix="kpl-dependency-seed-") as temporary:
            dockerfile = Path(temporary) / "Dockerfile"
            dockerfile.write_text(f"""FROM {source} AS installed
FROM {spec['base']} AS dependencies
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
LABEL {DEPENDENCY_LABEL}={spec['key']}
COPY --from=installed /usr/local /usr/local
COPY backend/requirements.txt backend/requirements-training.txt /app/backend/
RUN --network=none mkdir -p /opt/kpl-dependencies && printf '%s' '{spec['recipe']}' > /opt/kpl-dependencies/recipe.sha256
""")
            command([
                "docker", "buildx", "build", "--builder", "default", "--load",
                "--pull=false", "--network=none", "--progress=plain",
                "--platform", spec["platform"], "-f", str(dockerfile),
                "-t", destination, str(root),
            ])
    finally:
        # The original API/rollback tags remain. This temporary alias is unnecessary.
        subprocess.run(["docker", "image", "rm", source], text=True, capture_output=True)
    return True


def build(root, options):
    platform = command(
        ["docker", "version", "--format", "{{.Server.Os}}/{{.Server.Arch}}"],
        capture=True,
    ).stdout.strip()
    if not platform.startswith("linux/"):
        raise RuntimeError("Production dependencies require a Linux Docker engine.")
    spec = dependency_spec(root, platform)
    dependency_image = "kpl-api-deps:" + spec["key"]
    info = inspect_image(dependency_image)
    if info is not None and not options.rebuild_dependencies:
        if ((info.get("Config") or {}).get("Labels") or {}).get(DEPENDENCY_LABEL) != spec["key"]:
            raise RuntimeError(f"{dependency_image} has an unexpected dependency label.")
        print(f"Reusing installed dependencies: {dependency_image}", flush=True)
    else:
        migrated = False
        if not options.rebuild_dependencies:
            migrated = migrate_existing(root, spec, options.bootstrap_image, dependency_image)
        if not migrated:
            if options.require_reuse:
                raise RuntimeError("No matching installed dependency image; refusing package installation.")
            print("Building changed/missing dependencies. This step may download packages.", flush=True)
            args = [
                "docker", "buildx", "build", "--builder", "default", "--load",
                "--pull=false", "--progress=plain", "--platform", platform,
                "--target", "dependencies", "--build-arg", "DEPENDENCY_KEY=" + spec["key"],
                "-f", str(root / "backend/Dockerfile"), "-t", dependency_image,
            ]
            if options.rebuild_dependencies:
                args.append("--no-cache")
            command([*args, str(root)])
    command(["docker", "image", "tag", dependency_image, "kpl-api-deps:current"])
    # Override any stale visitor shell setting with the identity computed here.
    environment = {**os.environ, "KPL_API_DEPENDENCY_IMAGE": dependency_image}
    services = ["api"] if options.api_only else ["api", "web"]
    command([
        "docker", "compose", "--progress", "plain", "-f",
        str(root / "docker-compose.production.yml"), "build", "--builder", "default",
        *services,
    ], env=environment)
    print("Build complete. Deploy with:", flush=True)
    print("docker compose -f docker-compose.production.yml up -d --no-build --wait", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-only", action="store_true", help="Do not build the frontend.")
    parser.add_argument("--bootstrap-image", default="kpl-api:latest",
                        help="Local existing API image used for one-time package migration.")
    parser.add_argument("--require-reuse", action="store_true",
                        help="Stop instead of installing dependencies if no reusable image exists.")
    parser.add_argument("--rebuild-dependencies", action="store_true",
                        help="Explicitly rebuild dependencies without layer cache.")
    options = parser.parse_args()
    if options.rebuild_dependencies and options.require_reuse:
        parser.error("--rebuild-dependencies cannot be combined with --require-reuse")
    try:
        build(ROOT, options)
    except (RuntimeError, subprocess.CalledProcessError, OSError) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
