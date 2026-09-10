from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    actors,
    applications,
    credentials,
    dashboard,
    sources,
    subjects,
    tenants,
)
from app.core.config import get_settings
from app.core.errors import MemoraError

API_PREFIX = "/api/v1"

settings = get_settings()

app = FastAPI(
    title="Memora",
    version="0.1.0",
    description=(
        "Memora manages the ownership chain "
        "Tenant -> Application -> Actor -> Subject -> Source. "
        "Sources are registered and tracked; extraction, embeddings and the "
        "knowledge graph are later phases and are not part of this API."
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
):
    app.include_router(router, prefix=API_PREFIX)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
