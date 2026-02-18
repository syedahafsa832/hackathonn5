from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
import logging
import time
import uuid

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
        response = await call_next(request)

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
