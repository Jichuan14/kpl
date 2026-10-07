# Use BuildKit's bundled Dockerfile syntax; routine builds need no frontend pull.
# The default keeps standalone builds (including memory profiling) self-contained.
ARG DEPENDENCY_IMAGE=dependencies
FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f AS dependencies

ARG DEPENDENCY_KEY=standalone
LABEL io.draft-atlas.dependencies.key=$DEPENDENCY_KEY

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Keep the large, rarely changed training installation ahead of API dependencies.
# Cache downloads in the builder without adding them to the runtime image.
COPY backend/requirements-training.txt /app/backend/requirements-training.txt
RUN --mount=type=cache,id=kpl-pip,target=/root/.cache/pip,sharing=locked \
    pip install \
    --index-url https://download.pytorch.org/whl/cpu \
    -r /app/backend/requirements-training.txt

COPY backend/requirements.txt /app/backend/requirements.txt
RUN --mount=type=cache,id=kpl-pip,target=/root/.cache/pip,sharing=locked \
    pip install -r /app/backend/requirements.txt

# Record the dependency recipe so direct Compose builds detect base/recipe changes.
COPY backend/Dockerfile /tmp/kpl-dependency.Dockerfile
RUN --network=none python -c "import hashlib; from pathlib import Path; data = Path('/tmp/kpl-dependency.Dockerfile').read_bytes(); marker = ('FROM ' + chr(36) + '{DEPENDENCY_IMAGE} AS runtime' + chr(10)).encode(); directory = Path('/opt/kpl-dependencies'); directory.mkdir(parents=True, exist_ok=True); (directory / 'recipe.sha256').write_text(hashlib.sha256(data.split(marker)[0]).hexdigest())" \
    && rm /tmp/kpl-dependency.Dockerfile

FROM ${DEPENDENCY_IMAGE} AS runtime

# Refuse an outdated external dependency image, even for direct Compose builds.
# Compare before COPY backend replaces the manifests installed in the base image.
COPY backend/requirements.txt backend/requirements-training.txt backend/Dockerfile /tmp/kpl-required/
RUN --network=none python -c "from pathlib import Path; import hashlib, sys; names = ('requirements.txt', 'requirements-training.txt'); matches = all((Path('/tmp/kpl-required') / name).read_bytes() == (Path('/app/backend') / name).read_bytes() for name in names); marker = ('FROM ' + chr(36) + '{DEPENDENCY_IMAGE} AS runtime' + chr(10)).encode(); recipe = hashlib.sha256(Path('/tmp/kpl-required/Dockerfile').read_bytes().split(marker)[0]).hexdigest(); receipt = Path('/opt/kpl-dependencies/recipe.sha256'); matches = matches and receipt.is_file() and receipt.read_text() == recipe; sys.exit(0 if matches else 'Dependency image is outdated. Run python3 deploy/build-production.py.')" \
    && rm -rf /tmp/kpl-required

COPY backend /app/backend
COPY analysis /app/analysis
COPY knowledge/sources/official/herolist.json /app/knowledge/sources/official/herolist.json

WORKDIR /app/backend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

# SQLite has one writer at a time. One Uvicorn worker avoids separate
# application processes contending over the same ECS-local database file.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips=*"]
