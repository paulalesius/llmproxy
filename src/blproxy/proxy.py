"""BLProxy - routes requests to backends with global locking."""

import asyncio
import logging
import time

from dataclasses import dataclass
from typing import Optional

from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
import httpx

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from .backend import Backend
from .config import Config

logger = logging.getLogger("blproxy")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for logging all incoming HTTP requests and their final responses.
    
    Provides overview of every request with timing and status code.
    Backend-specific details (routing, locks, errors) are logged in _handle_request.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        start_time = time.time()
        client_host = request.client.host if request.client else "-"
        # Support X-Forwarded-For if behind reverse proxy
        forwarded = request.headers.get("x-forwarded-for", client_host)

        logger.info(
            f"→ {request.method} {request.url.path} from {forwarded}"
        )

        response = await call_next(request)

        process_time = (time.time() - start_time) * 1000  # milliseconds
        logger.info(
            f"← {request.method} {request.url.path} "
            f"status={response.status_code} ({process_time:.0f}ms)"
        )

        return response


@dataclass
class LockState:
    """Track which backend holds a lock."""
    locked_by: str  # Backend name that acquired the lock


class LockManager:
    """Manages global locks across backends using Condition for proper waiting."""
    
    def __init__(self, backends: dict[str, Backend], timeout: int = 300):
        self.backends = backends
        self.locks: dict[str, LockState] = {}
        self.condition = asyncio.Condition()
        self.timeout = timeout

    async def acquire(self, backend_name: str, lock_targets: list[str]) -> bool:
        """Acquire locks on specified backends.
        
        Waits efficiently using Condition until all targets are free or timeout occurs.
        Returns True if locks were acquired, False if timeout.
        """
        async with self.condition:
            try:
                await asyncio.wait_for(
                    self._wait_until_free(lock_targets),
                    timeout=self.timeout
                )
            except asyncio.TimeoutError:
                return False

            # All targets are now free – acquire them atomically
            for target in lock_targets:
                self.locks[target] = LockState(locked_by=backend_name)
            return True

    async def _wait_until_free(self, lock_targets: list[str]) -> None:
        """Wait (inside condition) until none of the lock_targets are currently locked."""
        while any(target in self.locks for target in lock_targets):
            await self.condition.wait()

    async def release(self, backend_name: str, lock_targets: list[str]) -> None:
        """Release locks held by backend and notify waiters."""
        async with self.condition:
            for target in lock_targets:
                if target in self.locks and self.locks[target].locked_by == backend_name:
                    del self.locks[target]
            # Notify all waiters that locks may be available
            self.condition.notify_all()

    def is_locked(self, backend_name: str) -> bool:
        """Check if a backend is currently locked."""
        return backend_name in self.locks


class LockProxy:
    """Main proxy server with connection pooling and proper streaming."""
    
    def __init__(self, config: Config):
        self.config = config
        
        # Convert backend configs to Backend instances
        self.backends: dict[str, Backend] = {}
        for name, backend_config in config.backends.items():
            self.backends[name] = Backend(
                name=name,
                url=backend_config.url,
                paths=backend_config.paths,
                locks=backend_config.locks
            )
        
        # Initialize lock manager
        self.lock_manager = LockManager(
            self.backends,
            timeout=config.global_lock.timeout
        ) if config.global_lock.enabled else None
        
        # Shared httpx client for connection pooling
        self.httpx_client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            timeout=httpx.Timeout(300.0, connect=30.0)
        )
        
        # Create FastAPI app
        self.app = FastAPI(title="BLProxy")
        self.app.add_middleware(RequestLoggingMiddleware)
        self._setup_routes()

    def _setup_routes(self) -> None:
        """Set up catch-all route for proxy."""
        
        @self.app.get("/")
        async def root():
            return {"name": "BLProxy", "version": "1.0.0"}
        
        @self.app.get("/health")
        async def health():
            return {"status": "healthy"}
        
        # Catch-all route - matches any path
        @self.app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
        async def proxy_request(request: Request, path: str):
            return await self._handle_request(request, path)

    async def _handle_request(self, request: Request, path: str) -> Response:
        """Handle incoming request - route to backend with locking and detailed logging."""
        full_path = f"/{path}"
        start_time = time.time()

        # Find matching backend
        backend: Optional[Backend] = None
        for b in self.backends.values():
            if b.matches_path(full_path):
                backend = b
                break

        if not backend:
            # Proxy-level 404 (no backend configured for this path)
            logger.warning(f"No backend matched path: {full_path} → returning 404 from BLProxy")
            return Response(status_code=404, content=f"Unknown path: {full_path}")

        # Get locks this backend needs
        lock_targets = backend.get_lock_targets(self.backends)

        # Acquire locks if enabled and configured
        acquired = True
        if self.lock_manager and lock_targets:
            logger.info(f"Acquiring locks {lock_targets} for backend '{backend.name}'")
            acquired = await self.lock_manager.acquire(backend.name, lock_targets)

        if not acquired:
            logger.warning(
                f"Lock timeout waiting for {lock_targets} (held by another backend) "
                f"→ returning 503 for {full_path}"
            )
            return Response(
                status_code=503,
                content=f"Backend {backend.name} is locked by another backend",
                headers={"Retry-After": "10"}
            )

        try:
            logger.info(f"Forwarding {request.method} {full_path} → {backend.name} ({backend.url})")

            # Avoid double slashes (e.g. http://host:port//v1/models)
            # Some llama.cpp servers are sensitive to this.
            # Note: backend.url is Pydantic AnyHttpUrl → must convert to str first
            target_url = f"{str(backend.url).rstrip('/')}{full_path}"

            # Filter hop-by-hop headers (do not forward these)
            hop_by_hop = {"connection", "keep-alive", "transfer-encoding", "upgrade", "trailers"}
            filtered_headers = {
                k: v for k, v in request.headers.items()
                if k.lower() not in hop_by_hop
            }

            req = self.httpx_client.build_request(
                method=request.method,
                url=target_url,
                headers=filtered_headers,
                content=await request.body()
            )

            response = await self.httpx_client.send(req, stream=True)

            elapsed = time.time() - start_time
            status_code = response.status_code
            content_type = response.headers.get("content-type", "")

            # Log backend response with distinction for errors
            if status_code >= 500:
                logger.error(
                    f"Backend '{backend.name}' returned {status_code} for {full_path} "
                    f"(took {elapsed:.3f}s)"
                )
            elif status_code >= 400:
                logger.warning(
                    f"Backend '{backend.name}' returned {status_code} for {full_path} "
                    f"(took {elapsed:.3f}s)  ← this is from the backend, not BLProxy"
                )
            else:
                logger.info(
                    f"Backend '{backend.name}' responded {status_code} for {full_path} "
                    f"(took {elapsed:.3f}s)"
                )

            if "text/event-stream" in content_type:
                # SSE streaming (e.g. chat completions)
                return StreamingResponse(
                    self._stream_sse(response.aiter_lines()),
                    status_code=status_code,
                    media_type="text/event-stream",
                    headers=dict(response.headers)
                )
            else:
                # Regular response (embeddings, models, etc.)
                return StreamingResponse(
                    self._stream_response(response.aiter_bytes()),
                    status_code=status_code,
                    headers=dict(response.headers)
                )

        except httpx.TimeoutException as e:
            logger.error(f"Backend '{backend.name}' timed out for {full_path}: {str(e)}")
            return Response(
                status_code=504,
                content=f"Backend {backend.name} timed out: {str(e)}"
            )
        except httpx.RequestError as e:
            logger.error(f"Backend '{backend.name}' connection error for {full_path}: {str(e)}")
            return Response(
                status_code=502,
                content=f"Backend {backend.name} error: {str(e)}"
            )

        finally:
            if self.lock_manager and lock_targets:
                await self.lock_manager.release(backend.name, lock_targets)
                logger.info(f"Released locks {lock_targets} for backend '{backend.name}'")

    async def _stream_sse(self, aiter_lines):
        """Stream SSE lines with proper formatting."""
        async for line in aiter_lines:
            yield line + "\n"

    async def _stream_response(self, aiter_bytes):
        """Stream response bytes."""
        async for chunk in aiter_bytes:
            yield chunk

    async def shutdown(self):
        """Clean up resources."""
        await self.httpx_client.aclose()

    async def run(self) -> None:
        """Run the proxy server."""
        import uvicorn
        
        config = uvicorn.Config(
            self.app,
            host=self.config.server.host,
            port=self.config.server.port,
            log_level="info",
            access_log=False,  # We use our own RequestLoggingMiddleware for cleaner request logs
        )
        server = uvicorn.Server(config)
        try:
            await server.serve()
        finally:
            await self.shutdown()
