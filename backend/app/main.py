from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    analytics,
    bp,
    coach,
    data,
    leagues,
    site_default,
    jobs,
    pipeline,
    simulation,
    sync,
    visualization,
)
from app.config import get_settings
from app.database import init_db
from app.services.pipeline_runner import runner as pipeline_runner

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
class CoachStreamGZipMiddleware(GZipMiddleware):
    """Do not buffer Draft Coach streaming responses."""

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path") == "/api/coach/stream":
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


app.add_middleware(CoachStreamGZipMiddleware, minimum_size=1000)

app.include_router(leagues.router)
app.include_router(site_default.router)
app.include_router(bp.router)
app.include_router(sync.router)
app.include_router(data.router)
app.include_router(pipeline.router)
app.include_router(jobs.router)
app.include_router(visualization.router)
app.include_router(simulation.router)
app.include_router(coach.router)
app.include_router(analytics.router)


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    pipeline_runner.start()


@app.on_event("shutdown")
def on_shutdown() -> None:
    pipeline_runner.stop()


@app.get("/health")
def health():
    if pipeline_runner.thread is not None and not pipeline_runner.thread.is_alive():
        return JSONResponse(status_code=503, content={"status": "degraded", "pipeline": "stopped"})
    return {"status": "ok"}
