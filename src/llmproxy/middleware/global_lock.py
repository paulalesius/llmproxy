"""Global lock middleware for backend coordination."""

import asyncio
import logging
from typing import Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response, JSONResponse
from starlette.status import HTTP_503_SERVICE_UNAVAILABLE

from ..config import get_config
from ..routing.backends import Backend, get_backend_for_path
from ..script_loader import load_lock_script, execute_lock_script

logger = logging.getLogger(__name__)


class GlobalLockMiddleware(BaseHTTPMiddleware):
    """Middleware that implements global locking between backends."""
    
    # Lock state
    _locks: dict[str, asyncio.Lock] = {}
    _lock_hooks: dict[str, dict] = {}  # backend_name -> loaded hook
    
    def __init__(self, app):
        super().__init__(app)
    
    async def _ensure_lock_initialized(self, backend: str) -> None:
        """Ensure lock exists for backend."""
        if backend not in self._locks:
            self._locks[backend] = asyncio.Lock()
    
    def _get_locking_backends(self, backend: str) -> list[str]:
        """Get list of backends that this backend locks (acquired when this backend is accessed)."""
        config = get_config()
        return config.lock.backends.get(backend, [])
    
    def _get_lock_script_for_backend(self, backend: str) -> Optional[str]:
        """Get lock script path for a backend (backend-specific or global)."""
        config = get_config()
        # Check backend-specific first
        backend_config = config.backends.get(backend)
        if backend_config and backend_config.lock_script:
            return backend_config.lock_script
        # Fall back to global
        return config.lock.lock_script
    
    async def _load_lock_hook(self, backend: str) -> Optional[dict]:
        """Load lock script hook for backend if configured."""
        script_path = self._get_lock_script_for_backend(backend)
        if not script_path:
            return None
        
        # Cache hook per backend
        if backend not in self._lock_hooks:
            hook = load_lock_script(script_path)
            self._lock_hooks[backend] = hook
            if hook.get("error"):
                logger.warning(f"Lock script for {backend}: {hook['error']}")
            else:
                logger.info(f"Loaded lock script for {backend}: {hook.get('type')}")
        
        return self._lock_hooks.get(backend)
    
    async def _run_lock_hook(
        self,
        backend: str,
        phase: str,
        request: Request,
        response_status: Optional[int] = None,
    ) -> None:
        """Run lock script hook (pre or post phase)."""
        hook = await self._load_lock_hook(backend)
        if not hook or hook.get("error"):
            return
        
        # Get config for global_lock_enabled flag
        config = get_config()
        
        # Build request data for hook
        request_data = {
            "phase": phase,
            "method": request.method,
            "path": request.url.path,
            "url": str(request.url),
            "headers": dict(request.headers),
            "backend": backend,
            "global_lock_enabled": config.lock.enabled,
        }
        
        if phase == "post" and response_status is not None:
            request_data["response_status"] = response_status
        
        # Execute hook
        result = execute_lock_script(hook, request_data)
        if result.get("error"):
            logger.warning(f"Lock hook {phase} for {backend}: {result['error']}")
        elif result.get("result"):
            logger.debug(f"Lock hook {phase} for {backend}: {result['result']}")
    
    async def _acquire_locks(self, backend: str) -> None:
        """Acquire locks for all backends that this one locks (blocking)."""
        locking_backends = self._get_locking_backends(backend)
        
        for locking_backend in sorted(locking_backends):
            await self._ensure_lock_initialized(locking_backend)
            await self._locks[locking_backend].acquire()
            logger.debug(f"Acquired lock for {locking_backend} (requested by {backend})")
    
    async def _release_locks(self, backend: str) -> None:
        """Release locks for all backends that this one locks."""
        locking_backends = self._get_locking_backends(backend)
        
        for locking_backend in sorted(locking_backends):
            if locking_backend in self._locks:
                try:
                    self._locks[locking_backend].release()
                    logger.debug(f"Released lock for {locking_backend} (requested by {backend})")
                except RuntimeError:
                    logger.warning(f"Lock for {locking_backend} not held by {backend}")
    
    async def _is_locked(self, backend: str) -> bool:
        """Check if any lock this backend needs to acquire is currently held."""
        locking_backends = self._get_locking_backends(backend)
        
        for locking_backend in locking_backends:
            if locking_backend in self._locks:
                if self._locks[locking_backend].locked():
                    return True
        return False
    
    async def dispatch(
        self,
        request: Request,
        call,
    ) -> Response:
        """Handle request with locking."""
        # Get backend for this path
        backend = get_backend_for_path(request.url.path)
        
        if backend is None:
            # No backend matched, pass through
            return await call(request)
        
        backend_name = backend.value
        
        # Get config for lock checks
        config = get_config()
        
        # Run pre-hook (always, regardless of enabled state)
        await self._run_lock_hook(backend_name, "pre", request)
        
        try:
            # Only acquire locks if enabled
            if config.lock.enabled:
                # Check locked_error mode
                if config.lock.locked_error:
                    if await self._is_locked(backend_name):
                        logger.info(f"Backend {backend_name} is locked, returning 503")
                        return JSONResponse(
                            status_code=HTTP_503_SERVICE_UNAVAILABLE,
                            content={
                                "error": {
                                    "message": f"Backend {backend_name} is currently locked",
                                    "type": "locked",
                                    "code": "backend_locked",
                                },
                                "retry_after": 5,
                            },
                        )
                
                # Acquire locks
                await self._acquire_locks(backend_name)
            
            # Process request
            response = await call(request)
            
            # Get status code for post-hook
            status_code = getattr(response, "status_code", None)
            
            # Run post-hook (always, regardless of enabled state)
            await self._run_lock_hook(backend_name, "post", request, status_code)
            
            return response
        
        finally:
            # Only release locks if enabled
            if config.lock.enabled:
                await self._release_locks(backend_name)