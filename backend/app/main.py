"""FastAPI application entry point.

Wires runtime configuration, database bootstrap, first-run administrator
provisioning, API routers, and static frontend hosting.

The app is mountable under a base-path prefix via ``APP_BASE_PREFIX`` (exposed
to FastAPI as ``root_path``) so it can run behind a reverse proxy that serves it
from a subpath and strips that prefix before forwarding.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import admin, audit
from .config import get_settings
from .db import get_connection, init_db
from .logging_config import configure_logging
from .routers import (
    applications,
    audit as audit_router,
    auth,
    settings as settings_router,
    users,
)

logger = logging.getLogger(__name__)

# Content Security Policy for the single-page app. Scripts and styles are served
# same-origin from the Vite build; application icons may be remote https images;
# everything else is locked to the origin. 'unsafe-inline' is permitted for
# styles only (React inline styles / SVG presentation), never for scripts.
_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "img-src 'self' https: data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)

# Interactive API docs (only exposed in dev mode) rely on inline scripts and a
# CDN, so the restrictive CSP is not applied to these paths.
_CSP_EXEMPT_PATHS = frozenset({"/docs", "/redoc", "/openapi.json"})

_HSTS_VALUE = "max-age=63072000; includeSubDomains"


def _system_event(action: str, detail: str = "") -> None:
    """Record a System-category audit event from process lifecycle code.

    Uses its own connection (the request-scoped ``get_db`` is not available
    here) and never raises -- a logging failure must not stop startup/shutdown.
    """
    try:
        with get_connection() as conn:
            audit.record(
                conn,
                category=audit.CATEGORY_SYSTEM,
                action=action,
                detail=detail,
            )
    except Exception:  # pragma: no cover - best-effort, never break lifecycle
        logger.exception("Failed to record system audit event action=%s", action)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.ensure_dirs()
    logger.info(
        "Starting AppManager Lite (auth=%s, dev=%s, base_prefix=%r)",
        settings.enable_auth,
        settings.dev_mode,
        settings.base_prefix or "/",
    )
    init_db()
    logger.info("Database ready at %s", settings.db_path)
    _system_event(
        "startup",
        detail=f"auth={settings.enable_auth} dev={settings.dev_mode}",
    )
    if settings.enable_auth:
        created = admin.ensure_first_run_admin()
        if created:
            logger.warning(
                "First-run administrator created. Credentials written to %s. "
                "The password must be changed at first login.",
                created,
            )
            _system_event("first_run_admin_created")
    else:
        logger.warning(
            "Authentication is DISABLED (APP_ENABLE_AUTH=0). Every request runs "
            "with administrator access. Do not use this mode in production."
        )
        _system_event("auth_disabled")
    yield
    logger.info("AppManager Lite shutting down")
    _system_event("shutdown")


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built single-page app, with a fallback to ``index.html``.

    If the frontend has not been built yet, a small JSON placeholder is served
    at the root so the API remains usable on its own.
    """
    settings = get_settings()
    dist = settings.frontend_dist
    index_file = dist / "index.html"

    if not index_file.is_file():

        @app.get("/", include_in_schema=False)
        def _placeholder() -> JSONResponse:
            return JSONResponse(
                {"status": "ok", "detail": "Frontend has not been built yet."}
            )

        return

    assets_dir = dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    # Pre-render index.html with a <base> tag derived from the configured base
    # prefix. The frontend is built with relative asset/API URLs, so a single
    # build resolves correctly under any prefix (root or behind a subpath proxy).
    base_href = f"{settings.base_prefix}/" if settings.base_prefix else "/"
    html = index_file.read_text(encoding="utf-8")
    if "<base " not in html:
        html = html.replace("<head>", f'<head>\n    <base href="{base_href}">', 1)
    index_html = html

    @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
    def spa(full_path: str) -> FileResponse | HTMLResponse:
        # Never let the SPA fallback shadow the API namespace.
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        # Serve a real static file when it exists and is safely inside dist;
        # otherwise return index.html so client-side rendering can take over.
        candidate = (dist / full_path).resolve()
        if full_path and dist in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return HTMLResponse(index_html)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    expose_docs = settings.dev_mode
    app = FastAPI(
        title="AppManager Lite",
        version="0.1.0",
        root_path=settings.base_prefix,
        docs_url="/docs" if expose_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if expose_docs else None,
        lifespan=lifespan,
    )

    app.include_router(auth.router, prefix="/api")
    app.include_router(users.router, prefix="/api")
    app.include_router(applications.router, prefix="/api")
    app.include_router(audit_router.router, prefix="/api")
    app.include_router(settings_router.router, prefix="/api")

    @app.middleware("http")
    async def security_headers(request: Request, call_next) -> Response:
        """Attach defensive HTTP response headers to every response."""
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "no-referrer")
        headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")

        path = request.url.path
        if path not in _CSP_EXEMPT_PATHS:
            headers.setdefault("Content-Security-Policy", _CONTENT_SECURITY_POLICY)
        # Never cache API responses; they can carry per-user data.
        if path == "/api" or path.startswith("/api/"):
            headers["Cache-Control"] = "no-store"
        # Advertise HSTS only when the deployment is serving over TLS, signalled
        # by secure cookies being enabled.
        if settings.secure_cookies:
            headers.setdefault("Strict-Transport-Security", _HSTS_VALUE)
        return response

    @app.get("/api/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    _mount_frontend(app)
    return app


app = create_app()
