"""API key authentication middleware."""

import logging

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from ..config import get_config

logger = logging.getLogger(__name__)


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Middleware that validates API key if configured."""

    def __init__(self, app):
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call,
    ) -> Response:
        """Handle request with API key validation."""
        config = get_config()

        # Skip if no API key configured
        if not config.api_key:
            return await call(request)

        # Get API key from header
        api_key = request.headers.get("X-API-Key")

        if not api_key:
            logger.warning("Request without API key")
            return JSONResponse(
                status_code=401,
                content={"detail": "API key required"},
                headers={"WWW-Authenticate": "X-API-Key"},
            )

        if api_key != config.api_key:
            logger.warning(f"Invalid API key: {api_key}")
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid API key"},
                headers={"WWW-Authenticate": "X-API-Key"},
            )

        logger.debug("API key validated")
        return await call(request)
