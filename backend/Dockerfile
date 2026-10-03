# syntax=docker/dockerfile:1
FROM python:3.12-slim

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
