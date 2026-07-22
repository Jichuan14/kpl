from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.api import bp, data, leagues, pipeline, sync, visualization
from app.config import get_settings
from app.database import init_db

settings = get_settings()

app = FastAPI(
    title="KPL BP Backend",
    description="Hero ban/pick ingest + stats API for KPL analysis",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

app.include_router(leagues.router)
app.include_router(bp.router)
app.include_router(sync.router)
app.include_router(data.router)
app.include_router(pipeline.router)
app.include_router(visualization.router)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
