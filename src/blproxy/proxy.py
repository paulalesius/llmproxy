"""BLProxy - routes requests to backends with global locking."""

import asyncio
from dataclasses import dataclass
from typing import Optional
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
import httpx

from .backend import Backend
from .config import Config


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
        """Handle incoming request - route to backend with locking."""
        
        # Find matching backend
        full_path = f"/{path}"
        backend: Optional[Backend] = None
        
        for b in self.backends.values():
            if b.matches_path(full_path):
                backend = b
                break
        
        if not backend:
            # No backend matched - return 404
            return Response(status_code=404, content=f"Unknown path: {full_path}")
        
        # Get locks this backend needs
        lock_targets = backend.get_lock_targets(self.backends)
        
        # Acquire locks if enabled
        acquired = True
        if self.lock_manager and lock_targets:
            acquired = await self.lock_manager.acquire(backend.name, lock_targets)
        
        if not acquired:
            # Timeout waiting for locks
            return Response(
                status_code=503,
                content=f"Backend {backend.name} is locked by another backend",
                headers={"Retry-After": "10"}
            )
        
        try:
            # Forward request to backend
            target_url = f"{backend.url}{full_path}"
            
            # Filter hop-by-hop headers
            hop_by_hop = {"connection", "keep-alive", "transfer-encoding", "upgrade", "trailers"}
            filtered_headers = {
                k: v for k, v in request.headers.items()
                if k.lower() not in hop_by_hop
            }
            
            # Build request
            req = self.httpx_client.build_request(
                method=request.method,
                url=target_url,
                headers=filtered_headers,
                content=await request.body()
            )
            
            # Send with streaming
            response = await self.httpx_client.send(req, stream=True)
            
            # Stream response back (works for SSE and regular responses)
            if response.headers.get("content-type") == "text/event-stream":
                return StreamingResponse(
                    self._stream_sse(response.aiter_lines()),
                    status_code=response.status_code,
                    headers=dict(response.headers)
                )
            else:
                # For non-streaming, still stream to avoid buffering
                return StreamingResponse(
                    self._stream_response(response.aiter_bytes()),
                    status_code=response.status_code,
                    headers=dict(response.headers)
                )
        
        except httpx.TimeoutException as e:
            return Response(
                status_code=504,
                content=f"Backend {backend.name} timed out: {str(e)}"
            )
        except httpx.RequestError as e:
            return Response(
                status_code=502,
                content=f"Backend {backend.name} error: {str(e)}"
            )
        
        finally:
            # Release locks
            if self.lock_manager and lock_targets:
                await self.lock_manager.release(backend.name, lock_targets)

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
            log_level="info"
        )
        server = uvicorn.Server(config)
        try:
            await server.serve()
        finally:
            await self.shutdown()
