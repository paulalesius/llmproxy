import logging
import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    """Enkel logging middleware som loggar request + response tid."""

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request, call_next):
        start_time = time.time()

        logger.debug(
            f"REQUEST: {request.method} {request.url.path}"
            f"{('?' + request.url.query) if request.url.query else ''}"
        )

        response = await call_next(request)

        duration = time.time() - start_time
        logger.debug(
            f"RESPONSE: {request.method} {request.url.path} "
            f"-> {response.status_code} ({duration:.3f}s)"
        )

        return response
