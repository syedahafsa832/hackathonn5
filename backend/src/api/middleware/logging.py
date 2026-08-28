from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.exceptions import HTTPException as StarletteHTTPException
import logging
import time
import uuid
import traceback

from src.services.admin_alert_service import notify_critical_error

# Configure logger
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Generate a unique request ID
        request_id = str(uuid.uuid4())

        # Log request start
        start_time = time.time()
        logger.info(f"[{request_id}] {request.method} {request.url.path}", extra={
            'request_id': request_id,
            'method': request.method,
            'path': request.url.path,
            'client_host': request.client.host,
            'client_port': request.client.port
        })

        # Process the request
        try:
            response = await call_next(request)
        except Exception as exc:
            # Only genuinely unexpected failures page anyone: an HTTPException
            # under 500 (400/401/403/404/422/etc) is normal, expected
            # application flow, not an incident. Anything else - or an
            # HTTPException carrying a 5xx - is.
            status_code = getattr(exc, "status_code", 500)
            if not (isinstance(exc, StarletteHTTPException) and status_code < 500):
                tenant = getattr(request.state, "tenant", None)
                notify_critical_error(
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    route=request.url.path,
                    method=request.method,
                    status_code=status_code,
                    request_id=request_id,
                    tenant_id=getattr(tenant, "tenant_id", None),
                    user_email=getattr(tenant, "email", None),
                    stack_trace=traceback.format_exc(),
                )
            raise

        # An HTTPException(5xx) raised in a route handler lands here too:
        # Starlette's ExceptionMiddleware (which sits between this
        # middleware and the router) already converts it into a Response
        # before we ever see it as an exception, so this - not the except
        # block above - is what actually catches those. The detail message
        # isn't available without unsafely re-reading the response stream,
        # so this alert carries route/method/status/timestamp but not the
        # original detail text; the except block above still gets the full
        # message/stack trace for a genuinely unhandled exception.
        if response.status_code >= 500:
            tenant = getattr(request.state, "tenant", None)
            notify_critical_error(
                error_type="HTTPError",
                error_message=f"Handler returned HTTP {response.status_code} without raising",
                route=request.url.path,
                method=request.method,
                status_code=response.status_code,
                request_id=request_id,
                tenant_id=getattr(tenant, "tenant_id", None),
                user_email=getattr(tenant, "email", None),
            )

        # Calculate duration
        duration = time.time() - start_time

        # Log response
        logger.info(f"[{request_id}] {response.status_code} - {duration:.2f}s", extra={
            'request_id': request_id,
            'status_code': response.status_code,
            'duration': duration
        })

        return response

def setup_logging_middleware(app: FastAPI):
    """
    Add logging middleware to the FastAPI application
    """
    app.add_middleware(LoggingMiddleware)
    return app
