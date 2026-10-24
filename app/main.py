from __future__ import annotations

import logging
import sqlite3
import ipaddress
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from app.api.middleware import RequestContextMiddleware
from app.api.v1 import router, directus_process
from app.core.config import Settings
from app.core.errors import WellarError
from app.core.logging import configure_logging, event
from app.core.logging import request_id_var
from app.observability.metrics import BoundedMetrics, worker_metrics
from app.core.adapters import create_analysis_processor,create_media_store,create_queue_store
from app.queue.sqlite_store import sqlite_error_details
from app.security.auth import DevelopmentAuthenticator, DirectusAuthenticator, JWTAuthenticator
from app.security.rate_limit import TenantRateLimiter
from app.drowsiness_indicator import load_thresholds
from app.compat.directus_facade import DirectusHTTPClient, DirectusPullProcessor

logger=logging.getLogger("wellar.app")


def create_app(settings: Settings | None=None, store=None, authenticator=None) -> FastAPI:
    settings=settings or Settings.from_env()
    settings.validate()
    load_thresholds()  # Reject an invalid indicator contract before serving traffic.
    configure_logging(settings.log_level,settings.environment)
    owns_store=store is None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.store is None:
            app.state.store = create_queue_store(settings)
        if app.state.store.readiness() is not True:
            raise RuntimeError("storage readiness check failed")
        app.state.media_store=create_media_store(settings,app.state.store)
        delegate=create_analysis_processor(settings,app.state.media_store)
        app.state.worker_processor=(DirectusPullProcessor(DirectusHTTPClient(settings.directus_url,settings.directus_service_token,timeout=settings.directus_timeout_seconds),app.state.media_store,delegate) if settings.directus_pull_enabled else delegate)
        app.state.ready=True
        event(logger,"application.started",processing_stage="startup")
        try: yield
        finally:
            app.state.ready=False
            if getattr(app.state,"worker_processor",None) is not None: app.state.worker_processor.close()
            if getattr(app.state,"media_store",None) is not None:
                media_store=app.state.media_store
                if hasattr(media_store,"aclose"): await media_store.aclose()
                elif hasattr(media_store,"close"): media_store.close()
            if owns_store: app.state.store.close()
            event(logger,"application.stopped",processing_stage="shutdown")

    app=FastAPI(title="Wellar AI V2",version="0.1.0",lifespan=lifespan)
    app.state.settings=settings
    app.state.store=store
    app.state.media_store=None
    if authenticator is not None: app.state.authenticator=authenticator
    elif settings.auth_mode=="oidc":
        app.state.authenticator=JWTAuthenticator(issuer=settings.oidc_issuer,audience=settings.oidc_audience,jwks_uri=settings.oidc_jwks_uri,tenant_claim=settings.oidc_tenant_claim,algorithms=settings.oidc_algorithms)
    elif settings.auth_mode=="directus":
        app.state.authenticator=DirectusAuthenticator(base_url=settings.directus_url,timeout=settings.directus_timeout_seconds)
    else: app.state.authenticator=DevelopmentAuthenticator(settings.dev_auth_tokens or {})
    app.state.ready=False
    app.state.metrics=BoundedMetrics()
    app.state.directus_pull_enabled=settings.directus_pull_enabled
    try:
        rate_limit_per_minute=int(__import__("os").getenv("WELLAR_RATE_LIMIT_PER_MINUTE","120"))
    except ValueError as exc:
        raise ValueError("WELLAR_RATE_LIMIT_PER_MINUTE must be a positive integer") from exc
    app.state.rate_limiter=TenantRateLimiter(rate_limit_per_minute)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(router)
    app.add_api_route("/process", directus_process, methods=["POST"], status_code=202)

    @app.exception_handler(WellarError)
    async def wellar_error_handler(request: Request, exc: WellarError):
        headers={"Retry-After":"1"} if exc.status_code in {429,503} else None
        return JSONResponse(status_code=exc.status_code,headers=headers,content={"error":{"code":exc.code,"message":exc.message,"retryable":exc.retryable,"request_id":request_id_var.get()}})

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        detail=exc.detail if isinstance(exc.detail,dict) else {"code":"http_error","message":str(exc.detail)}
        return JSONResponse(status_code=exc.status_code,headers=exc.headers,content={"error":{"code":detail.get("code","http_error"),"message":detail.get("message","Request failed"),"retryable":bool(detail.get("retryable",exc.status_code>=500)),"request_id":request_id_var.get()}})

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Request,exc: RequestValidationError):
        return JSONResponse(status_code=422,content={"error":{"code":"validation_error","message":"Request fields are invalid","retryable":False,"request_id":request_id_var.get()}})

    @app.exception_handler(sqlite3.Error)
    async def sqlite_error_handler(request: Request, exc: sqlite3.Error):
        code,name,transient=sqlite_error_details(exc)
        error_code="queue_temporarily_unavailable" if transient else "queue_storage_error"
        event(logger,"sqlite.request.failed",logging.WARNING if transient else logging.ERROR,
             processing_stage="queue_storage",error_code=error_code,
             exception_type=type(exc).__name__,sqlite_error_code=code,sqlite_error_name=name,
             retry_attempt_count=0,attempt_count=1)
        if transient:
            return JSONResponse(status_code=503,content={"error":{"code":error_code,"message":"Queue storage is temporarily busy; retry the request with the same idempotency key","retryable":True,"request_id":request_id_var.get()}})
        return JSONResponse(status_code=500,content={"error":{"code":error_code,"message":"Queue storage could not complete the request","retryable":False,"request_id":request_id_var.get()}})

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request,exc: Exception):
        event(logger,"request.failed.unexpected",logging.ERROR,processing_stage="http",error_code="internal_error",exception_type=type(exc).__name__)
        return JSONResponse(status_code=500,content={"error":{"code":"internal_error","message":"Request could not be completed","retryable":True,"request_id":request_id_var.get()}})

    @app.get("/health")
    def health(): return {"status":"ok"}

    @app.get("/metrics",include_in_schema=False)
    def metrics():
        rows=[]
        counters={**app.state.metrics.snapshot(),**worker_metrics.snapshot()}
        for name,value in sorted(counters.items()):
            metric=name.replace("-","_")
            rows.append(f'wellar_counter{{name="{metric}"}} {int(value)}')
        try:
            diagnostics=app.state.store.diagnostics()
        except Exception:
            diagnostics={}
        for name,value in sorted(diagnostics.items()):
            if isinstance(value,(int,float)) and not isinstance(value,bool):
                safe_name="".join(ch if ch.isalnum() or ch=="_" else "_" for ch in name)
                rows.append(f"wellar_queue_{safe_name} {value}")
        return PlainTextResponse("\n".join(rows)+"\n",media_type="text/plain; version=0.0.4")

    @app.get("/self-test", response_class=HTMLResponse, include_in_schema=False)
    def local_self_test(request: Request):
        client=request.client
        try: local_only=client is not None and ipaddress.ip_address(client.host).is_loopback
        except ValueError: local_only=False
        if settings.environment != "test" or not local_only:
            raise HTTPException(404,detail="Not found")
        page=Path(__file__).with_name("self_test.html").read_text(encoding="utf-8")
        return HTMLResponse(page,headers={
            "Cache-Control":"no-store, max-age=0",
            "Pragma":"no-cache",
            "Content-Security-Policy":"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' blob: data:; media-src blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            "X-Content-Type-Options":"nosniff",
            "Referrer-Policy":"no-referrer",
        })

    @app.get("/ready")
    def ready():
        queue_diagnostics=None
        try:
            storage_ok=app.state.store is not None and app.state.store.readiness()
            if storage_ok and hasattr(app.state.store,"diagnostics"):
                queue_diagnostics=app.state.store.diagnostics()
        except Exception: storage_ok=False
        try: media_ok=app.state.media_store is not None and app.state.media_store.readiness()
        except Exception: media_ok=False
        dependencies={"queue":bool(storage_ok),"media_storage":bool(media_ok)}
        if not app.state.ready or not all(dependencies.values()):
            return JSONResponse(status_code=503,content={"status":"not_ready","dependencies":dependencies,"queue":queue_diagnostics})
        return {"status":"ready","dependencies":dependencies,"queue":queue_diagnostics}

    return app


app=create_app()
