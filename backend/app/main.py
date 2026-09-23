import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    auth,
    actors,
    applications,
    credentials,
    dashboard,
    extractions,
    sources,
    subjects,
    tenants,
    usage,
)
from app.core.config import get_settings
from app.core.errors import MemoraError

API_PREFIX = "/api/v1"

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # A malformed price list must stop the server, not silently mis-price runs.
    from app.core.pricing import get_price_list

    get_price_list()

    # Extraction runs are background tasks in this process; any still marked
    # `processing` at startup were cut off by a restart and will never finish.
    from app.db.database import SessionLocal
    from app.services.extraction_service import fail_interrupted_runs

    try:
        with SessionLocal() as db:
            interrupted = fail_interrupted_runs(db)
        if interrupted:
            logging.getLogger(__name__).warning(
                "marked %d interrupted extraction run(s) as failed", interrupted
            )
    except Exception:
        logging.getLogger(__name__).exception("could not check for interrupted runs")
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Memora",
    version="0.1.0",
    description=(
        "Memora manages the ownership chain "
        "Tenant -> Application -> Actor -> Subject -> Source. "
        "Sources are registered and tracked, and the Extraction Agent turns a "
        "stored document into versioned, structured information. Chunks, "
        "embeddings and the knowledge graph are later phases."
    ),
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(MemoraError)
def handle_memora_error(_: Request, exc: MemoraError) -> JSONResponse:
    """Turn domain errors into consistent JSON, so the console can rely on
    `detail` being present on every failure regardless of its origin."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message, "code": exc.code},
    )


# Tables are created by Alembic only. Nothing here calls create_all(): an app
# that silently creates its own schema on boot will drift from the migration
# history, and the difference only surfaces on a fresh database.
for router in (
    auth.router,
    dashboard.router,
    tenants.router,
    applications.tenant_router,
    applications.router,
    credentials.application_router,
    credentials.router,
    actors.application_router,
    actors.router,
    subjects.application_router,
    subjects.router,
    sources.subject_router,
    sources.router,
    extractions.router,
    usage.router,
):
    app.include_router(router, prefix=API_PREFIX)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
