"""Logging middleware with debug support."""

import logging
import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        config = None
        try:
            from ..config import get_config
            config = get_config()
        except Exception:
            pass

        log_requests = getattr(config, "log_requests", True) if config else True

        if log_requests:
            logger.info(f"→ {request.method} {request.url.path}")

        start = time.time()
        response = await call_next(request)
        duration = time.time() - start

        status = response.status_code
        logger.info(f"← {request.method} {request.url.path} {status} ({duration:.3f}s)")

        return response
