from __future__ import annotations

import logging
import re
import time
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from opentelemetry import propagate,trace

from app.core.logging import event, request_id_var, trace_id_var

logger = logging.getLogger("wellar.request")
_VALID_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_TRACEPARENT=re.compile(r"^[0-9a-fA-F]{2}-([0-9a-fA-F]{32})-[0-9a-fA-F]{16}-[0-9a-fA-F]{2}$")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        candidate = request.headers.get("X-Request-ID", "")
        request_id = candidate if _VALID_ID.fullmatch(candidate) else str(uuid4())
        token = request_id_var.set(request_id)
        raw_traceparent=request.headers.get("traceparent","")
        match=_TRACEPARENT.fullmatch(raw_traceparent)
        trace_id=match.group(1).lower() if match and int(match.group(1),16)!=0 else "-"
        trace_token=trace_id_var.set(trace_id)
        started = time.perf_counter()
        try:
            parent=propagate.extract(request.headers)
            with trace.get_tracer("wellar.api").start_as_current_span("http.server.request",context=parent) as span:
                span.set_attribute("http.request.method",request.method)
                span.set_attribute("url.path",request.url.path)
                response = await call_next(request)
                response.headers["X-Request-ID"] = request_id
                span.set_attribute("http.response.status_code",response.status_code)
                event(logger, "http.request.completed", status_code=response.status_code, duration_ms=round((time.perf_counter()-started)*1000, 3), processing_stage="http")
                return response
        finally:
            request_id_var.reset(token)
            trace_id_var.reset(trace_token)
