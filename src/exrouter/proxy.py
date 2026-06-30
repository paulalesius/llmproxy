"""EXRouter - routes requests to backends with global locking and optional request remapping."""

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
from .hooks import HookLoader, HookContext
from .remapper import RemapperLoader, RemapResult

logger = logging.getLogger("exrouter")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for logging all incoming HTTP requests and their final responses."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        start_time = time.time()
        client_host = request.client.host if request.client else "-"
        forwarded = request.headers.get("x-forwarded-for", client_host)

        logger.info(f"→ {request.method} {request.url.path} from {forwarded}")

        response = await call_next(request)

        process_time = (time.time() - start_time) * 1000
        logger.info(
            f"← {request.method} {request.url.path} "
            f"status={response.status_code} ({process_time:.0f}ms)"
        )

        return response


@dataclass
class LockState:
    """Track which backend holds a lock."""
    locked_by: str


class LockManager:
    """Manages global locks across backends using Condition for proper waiting."""

    def __init__(self, backends: dict[str, Backend], timeout: int = 300):
        self.backends = backends
        self.locks: dict[str, LockState] = {}
        self.condition = asyncio.Condition()
        self.timeout = timeout

    async def acquire(self, backend_name: str, lock_targets: list[str]) -> bool:
        async with self.condition:
            try:
                await asyncio.wait_for(
                    self._wait_until_free(backend_name, lock_targets),
                    timeout=self.timeout
                )
            except asyncio.TimeoutError:
                return False

            for target in lock_targets:
                self.locks[target] = LockState(locked_by=backend_name)
            return True

    async def _wait_until_free(self, backend_name: str, lock_targets: list[str]) -> None:
        while any(
            target in self.locks and self.locks[target].locked_by != backend_name
            for target in lock_targets
        ):
            await self.condition.wait()

    async def release(self, backend_name: str, lock_targets: list[str]) -> None:
        async with self.condition:
            for target in lock_targets:
                if target in self.locks and self.locks[target].locked_by == backend_name:
                    del self.locks[target]
            self.condition.notify_all()

    def is_locked(self, backend_name: str) -> bool:
        return backend_name in self.locks


class LockProxy:
    """Main proxy server with connection pooling, locking, hooks, and request remapping."""

    def __init__(self, config: Config):
        self.config = config

        # Convert backend configs to Backend instances
        self.backends: dict[str, Backend] = {}
        for name, backend_config in config.backends.items():
            self.backends[name] = Backend(
                name=name,
                url=str(backend_config.url),
                paths=backend_config.paths,
                locks=backend_config.locks,
                script=backend_config.script,
                remapper=backend_config.remapper,
            )

        # Initialize lock manager
        self.lock_manager = LockManager(
            self.backends,
            timeout=config.global_lock.timeout
        ) if config.global_lock.enabled else None

        # Initialize hook loader
        self.hook_loader = HookLoader()
        for backend in self.backends.values():
            if backend.script:
                self.hook_loader.load_script(backend.name, backend.script)

        # NEW: Initialize remapper loader
        self.remapper_loader = RemapperLoader()
        for backend in self.backends.values():
            if backend.remapper:
                self.remapper_loader.load_script(backend.name, backend.remapper)

        # Track in-flight requests per backend
        self.active_counts: dict[str, int] = {name: 0 for name in self.backends}

        # Shared httpx client
        self.httpx_client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            timeout=httpx.Timeout(300.0, connect=30.0)
        )

        # Create FastAPI app
        self.app = FastAPI(title="EXRouter")
        self.app.add_middleware(RequestLoggingMiddleware)
        self._setup_routes()

    def _setup_routes(self) -> None:
        @self.app.get("/")
        async def root():
            return {"name": "EXRouter", "version": "1.0.0"}

        @self.app.get("/health")
        async def health():
            return {"status": "healthy"}

        @self.app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
        async def proxy_request(request: Request, path: str):
            return await self._handle_request(request, path)

    async def _handle_request(self, request: Request, path: str) -> Response:
        full_path = f"/{path}"
        start_time = time.time()

        # 1. Find matching backend by path
        backend: Optional[Backend] = None
        for b in self.backends.values():
            if b.matches_path(full_path):
                backend = b
                break

        if not backend:
            logger.warning(f"No backend matched path: {full_path} → returning 404 from EXRouter")
            return Response(status_code=404, content=f"Unknown path: {full_path}")

        # 2. NEW: Run request remapper (if configured) BEFORE acquiring locks
        request_body = await request.body()
        hook_context = HookContext(
            backend_name=backend.name,
            request_method=request.method,
            request_path=full_path,
            request_headers=dict(request.headers),
            request_body=request_body
        )

        remapped = False
        if backend.remapper:
            remapper_instance = self.remapper_loader.get_remapper(backend.name)
            if remapper_instance:
                result: Optional[RemapResult] = await self.remapper_loader.call_remap(
                    remapper_instance, hook_context
                )
                if result:
                    remapped = True
                    # Short-circuit with a direct response?
                    if result.status_code is not None:
                        content = result.content
                        if isinstance(content, str):
                            content = content.encode("utf-8")
                        return Response(
                            status_code=result.status_code,
                            content=content or b"",
                            headers=result.response_headers or {}
                        )

                    # Switch backend?
                    if result.backend and result.backend in self.backends:
                        backend = self.backends[result.backend]
                        hook_context.backend_name = backend.name  # update context
                        logger.info(f"Remapped request to backend '{backend.name}'")

                    # Rewrite path?
                    if result.path is not None:
                        full_path = result.path
                        hook_context.request_path = full_path

                    # Apply other modifications
                    if result.method:
                        # We can't easily change method after body read, but we can log it
                        logger.info(f"Remapper requested method change to {result.method} (not fully supported yet)")
                    if result.headers:
                        hook_context.request_headers.update(result.headers)
                    if result.body is not None:
                        hook_context.request_body = result.body
                        request_body = result.body  # use for forwarding

        # 3. Get locks for the (possibly remapped) backend
        lock_targets = backend.get_lock_targets(self.backends)

        # 4. Acquire locks
        acquired = True
        if self.lock_manager and lock_targets:
            logger.info(f"Acquiring locks {lock_targets} for backend '{backend.name}'")
            acquired = await self.lock_manager.acquire(backend.name, lock_targets)

        if not acquired:
            logger.warning(
                f"Lock timeout waiting for {lock_targets} → returning 503 for {full_path}"
            )
            return Response(
                status_code=503,
                content=f"Backend {backend.name} is locked by another backend",
                headers={"Retry-After": "10"}
            )

        # 5. Backend activation tracking
        was_active = self.active_counts.get(backend.name, 0) > 0
        self.active_counts[backend.name] = self.active_counts.get(backend.name, 0) + 1
        if not was_active:
            if backend.script:
                await self.hook_loader.call_hook(
                    self.hook_loader.get_hook(backend.name),
                    "on_backend_activated",
                    hook_context
                )

        try:
            # Call lifecycle hooks on the final backend
            if backend.script:
                await self.hook_loader.call_hook(
                    self.hook_loader.get_hook(backend.name),
                    "on_locks_acquired",
                    hook_context
                )
                await self.hook_loader.call_hook(
                    self.hook_loader.get_hook(backend.name),
                    "on_before_request",
                    hook_context
                )

            logger.info(f"Forwarding {request.method} {full_path} → {backend.name} ({backend.url})")

            target_url = f"{str(backend.url).rstrip('/')}{full_path}"

            hop_by_hop = {"connection", "keep-alive", "transfer-encoding", "upgrade", "trailers"}
            filtered_headers = {
                k: v for k, v in hook_context.request_headers.items()
                if k.lower() not in hop_by_hop
            }

            req = self.httpx_client.build_request(
                method=request.method,
                url=target_url,
                headers=filtered_headers,
                content=hook_context.request_body or request_body
            )

            response = await self.httpx_client.send(req, stream=True)

            elapsed = time.time() - start_time
            status_code = response.status_code
            content_type = response.headers.get("content-type", "")

            hook_context.response_status = status_code
            hook_context.response_headers = dict(response.headers)

            if backend.script:
                await self.hook_loader.call_hook(
                    self.hook_loader.get_hook(backend.name),
                    "on_response",
                    hook_context
                )

            if status_code >= 500:
                logger.error(f"Backend '{backend.name}' returned {status_code} for {full_path} (took {elapsed:.3f}s)")
            elif status_code >= 400:
                logger.warning(f"Backend '{backend.name}' returned {status_code} for {full_path} (took {elapsed:.3f}s)")
            else:
                logger.info(f"Backend '{backend.name}' responded {status_code} for {full_path} (took {elapsed:.3f}s)")

            if "text/event-stream" in content_type:
                return StreamingResponse(
                    self._stream_sse(response.aiter_lines()),
                    status_code=status_code,
                    media_type="text/event-stream",
                    headers=dict(response.headers)
                )
            else:
                return StreamingResponse(
                    self._stream_response(response.aiter_bytes()),
                    status_code=status_code,
                    headers=dict(response.headers)
                )

        except httpx.TimeoutException as e:
            hook_context.error = f"Timeout: {str(e)}"
            logger.error(f"Backend '{backend.name}' timed out for {full_path}: {str(e)}")
            return Response(status_code=504, content=f"Backend {backend.name} timed out: {str(e)}")

        except httpx.RequestError as e:
            hook_context.error = f"RequestError: {str(e)}"
            logger.error(f"Backend '{backend.name}' connection error for {full_path}: {str(e)}")
            return Response(status_code=502, content=f"Backend {backend.name} error: {str(e)}")

        finally:
            if backend.script:
                await self.hook_loader.call_hook(
                    self.hook_loader.get_hook(backend.name),
                    "on_after_request",
                    hook_context
                )

            if self.lock_manager and lock_targets:
                await self.lock_manager.release(backend.name, lock_targets)
                logger.info(f"Released locks {lock_targets} for backend '{backend.name}'")

            if backend.script:
                await self.hook_loader.call_hook(
                    self.hook_loader.get_hook(backend.name),
                    "on_locks_released",
                    hook_context
                )

            # Deactivation tracking
            self.active_counts[backend.name] = self.active_counts.get(backend.name, 1) - 1
            if self.active_counts[backend.name] <= 0:
                self.active_counts[backend.name] = 0
                if backend.script:
                    await self.hook_loader.call_hook(
                        self.hook_loader.get_hook(backend.name),
                        "on_backend_deactivated",
                        hook_context
                    )

    async def _stream_sse(self, aiter_lines):
        async for line in aiter_lines:
            yield line + "\n"

    async def _stream_response(self, aiter_bytes):
        async for chunk in aiter_bytes:
            yield chunk

    async def shutdown(self):
        await self.httpx_client.aclose()

    async def run(self) -> None:
        import uvicorn
        config = uvicorn.Config(
            self.app,
            host=self.config.server.host,
            port=self.config.server.port,
            log_level="info",
            access_log=False,
        )
        server = uvicorn.Server(config)
        try:
            await server.serve()
        finally:
            await self.shutdown()
