"""LLM Proxy - Multi-service proxy server."""

import os
import logging
import asyncio
from contextlib import asynccontextmanager
from typing import Dict, List, Optional
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware
from uvicorn import Config, Server
import yaml
from .components.tei import TEIComponent
from .components.openai import OpenAIComponent
from .components.embeddings import EmbeddingsComponent
from .script_loader import load_script_from_path, execute_hook

# API key configuration
# API key protection is enabled when LLMPROXY_API_KEY is set
LLMPROXY_PORT = os.environ.get("LLMPROXY_PORT")
LLMPROXY_API_KEY = os.environ.get("LLMPROXY_API_KEY", "").strip()
API_KEY_ENABLED = bool(LLMPROXY_API_KEY)  # Enabled when API key is set

# Configure logging level from environment
log_level = os.environ.get("LLMPROXY_LOG_LEVEL", "info").lower()
LOG_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "trace": logging.DEBUG  # trace uses DEBUG level but we filter in code
}
logging.basicConfig(
    level=LOG_LEVELS.get(log_level, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Global lock configuration
# Only enabled if LLMPROXY_LOCK_CONFIG env var is explicitly set
# If not set, runs without any locks (disabled by default)
LOCK_CONFIG_PATH = os.environ.get("LLMPROXY_LOCK_CONFIG")

# Pre/post request Python script hooks
REQUEST_PRE_PYSCRIPT = os.environ.get("LLMPROXY_REQUEST_PRE_PYSCRIPT", "")
REQUEST_POST_PYSCRIPT = os.environ.get("LLMPROXY_REQUEST_POST_PYSCRIPT", "")

# Lock state - initialized in lifespan
lock_config: Optional[dict] = None
group_locks: Dict[str, asyncio.Lock] = {}
path_to_group: Dict[str, str] = {}

# Script hooks - initialized in lifespan
pre_script_hook: Optional[dict] = None
post_script_hook: Optional[dict] = None


def load_lock_config():
    """Load global lock configuration from YAML file."""
    global lock_config, group_locks, path_to_group

    if not LOCK_CONFIG_PATH:
        logger.info("Global lock disabled (LLMPROXY_LOCK_CONFIG not set)")
        lock_config = {"enabled": False}
        return

    if not os.path.exists(LOCK_CONFIG_PATH):
        logger.warning(f"Lock config not found at {LOCK_CONFIG_PATH}, running without locks")
        lock_config = {"enabled": False}
        return
    
    try:
        with open(LOCK_CONFIG_PATH, "r") as f:
            config = yaml.safe_load(f) or {}
        
        lock_config = config.get("global_lock")
        
        # Default: disabled unless explicitly enabled
        if lock_config is None:
            logger.info("Global lock: no config found, disabled by default")
            lock_config = {"enabled": False}
            return
        
        if not lock_config.get("enabled", False):
            logger.info("Global lock: config found but not enabled")
            return
        
        # Each path gets its own lock
        # When a path runs, it acquires its own lock + all locks in its 'locks' list
        path_to_group = {}
        group_locks = {}
        
        for path, config_entry in lock_config.items():
            if path == "enabled":
                continue
            
            # Create a lock for this path if it doesn't exist
            if path not in group_locks:
                group_locks[path] = asyncio.Lock()
            
            path_to_group[path] = path  # Path maps to its own lock
            
            if isinstance(config_entry, dict):
                locks = config_entry.get("locks", [])
                if locks:
                    logger.info(f"Endpoint {path} locks: {locks}")
                else:
                    logger.info(f"Endpoint {path} has no locks (runs freely)")
            elif isinstance(config_entry, str):
                locks = [config_entry]
                logger.info(f"Endpoint {path} locks: {locks}")
            else:
                logger.info(f"Endpoint {path} has no locks (runs freely)")
        
        logger.info(f"Global lock enabled with {len(group_locks)} locks")
        
    except Exception as e:
        logger.error(f"Failed to load lock config: {e}")

def load_script_hooks():
    """Load pre/post request Python script hooks."""
    global pre_script_hook, post_script_hook
    
    # Load pre script
    if REQUEST_PRE_PYSCRIPT:
        pre_script_hook = load_script_from_path(REQUEST_PRE_PYSCRIPT)
        if pre_script_hook["error"]:
            logger.warning(f"Pre-script hook: {pre_script_hook['error']}")
        else:
            logger.info(f"Pre-script hook loaded from {REQUEST_PRE_PYSCRIPT}")
            if pre_script_hook["handle_request"]:
                logger.info("  - has handle_request() function")
            else:
                logger.info("  - runs as plain script on import")
    else:
        pre_script_hook = None
        logger.info("Pre-script hook disabled (LLMPROXY_REQUEST_PRE_PYSCRIPT not set)")
    
    # Load post script
    if REQUEST_POST_PYSCRIPT:
        post_script_hook = load_script_from_path(REQUEST_POST_PYSCRIPT)
        if post_script_hook["error"]:
            logger.warning(f"Post-script hook: {post_script_hook['error']}")
        else:
            logger.info(f"Post-script hook loaded from {REQUEST_POST_PYSCRIPT}")
            if post_script_hook["handle_request"]:
                logger.info("  - has handle_request() function")
            else:
                logger.info("  - runs as plain script on import")
    else:
        post_script_hook = None
        logger.info("Post-script hook disabled (LLMPROXY_REQUEST_POST_PYSCRIPT not set)")


class GlobalLockMiddleware(BaseHTTPMiddleware):
    """Middleware that applies global locks based on path configuration.
    
    Each endpoint acquires its own lock + all locks listed in its config.
    This ensures mutual exclusion between endpoints that lock each other.
    
    If locked_error is enabled, returns 503 instead of blocking.
    """
    
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        
        # Check if this path has lock configuration
        if lock_config and lock_config.get("enabled") and path in path_to_group:
            # Get the locks this path needs to acquire
            locks_to_acquire = [group_locks[path]]  # Always acquire own lock
            
            # Add locks from config
            config_entry = lock_config.get(path, {})
            if isinstance(config_entry, dict):
                for locked_path in config_entry.get("locks", []):
                    if locked_path in group_locks:
                        locks_to_acquire.append(group_locks[locked_path])
            
            # Sort by lock id to ensure consistent ordering (avoid deadlock)
            locks_to_acquire = sorted(locks_to_acquire, key=id)
            
            # Check if locked_error mode is enabled
            locked_error = lock_config.get("locked_error", False)
            
            if locked_error:
                # Check if all locks are available (non-blocking)
                for lock in locks_to_acquire:
                    if lock.locked():
                        logger.debug(f"[{request.method} {path}] Lock busy, returning 503")
                        return JSONResponse(
                            status_code=503,
                            content={
                                "error": {
                                    "message": f"Service temporarily busy, endpoint {path} is locked",
                                    "type": "service_busy",
                                    "retry_after": 2  # seconds
                                }
                            }
                        )
                
                # All locks available, acquire them
                logger.debug(f"[{request.method} {path}] All locks available, acquiring {len(locks_to_acquire)}")
                for lock in locks_to_acquire:
                    await lock.acquire()
                
                try:
                    # Run pre-script hook (after lock acquired, before request)
                    if pre_script_hook:
                        request_data = {
                            "method": request.method,
                            "path": request.url.path,
                            "url": str(request.url),
                            "headers": dict(request.headers),
                        }
                        result = execute_hook(pre_script_hook, request_data)
                        if not result["success"]:
                            logger.warning(f"Pre-script hook failed: {result['error']}")
                        elif result["result"] is not None:
                            logger.debug(f"Pre-script hook returned: {result['result']}")
                    
                    response = await call_next(request)
                    
                    # Run post-script hook (after request, before lock release)
                    if post_script_hook:
                        request_data = {
                            "method": request.method,
                            "path": request.url.path,
                            "url": str(request.url),
                            "headers": dict(request.headers),
                            "response_status": response.status_code,
                        }
                        result = execute_hook(post_script_hook, request_data)
                        if not result["success"]:
                            logger.warning(f"Post-script hook failed: {result['error']}")
                        elif result["result"] is not None:
                            logger.debug(f"Post-script hook returned: {result['result']}")
                    
                    return response
                finally:
                    # Release all locks in reverse order
                    for lock in reversed(locks_to_acquire):
                        lock.release()
            else:
                # Blocking mode - wait for all locks
                if len(locks_to_acquire) > 1:
                    logger.debug(f"[{request.method} {path}] Acquiring {len(locks_to_acquire)} locks (blocking)")
                
                for lock in locks_to_acquire:
                    await lock.acquire()
                
                try:
                    # Run pre-script hook (after lock acquired, before request)
                    if pre_script_hook:
                        request_data = {
                            "method": request.method,
                            "path": request.url.path,
                            "url": str(request.url),
                            "headers": dict(request.headers),
                        }
                        result = execute_hook(pre_script_hook, request_data)
                        if not result["success"]:
                            logger.warning(f"Pre-script hook failed: {result['error']}")
                        elif result["result"] is not None:
                            logger.debug(f"Pre-script hook returned: {result['result']}")
                    
                    response = await call_next(request)
                    
                    # Run post-script hook (after request, before lock release)
                    if post_script_hook:
                        request_data = {
                            "method": request.method,
                            "path": request.url.path,
                            "url": str(request.url),
                            "headers": dict(request.headers),
                            "response_status": response.status_code,
                        }
                        result = execute_hook(post_script_hook, request_data)
                        if not result["success"]:
                            logger.warning(f"Post-script hook failed: {result['error']}")
                        elif result["result"] is not None:
                            logger.debug(f"Post-script hook returned: {result['result']}")
                    
                    return response
                finally:
                    # Release all locks in reverse order
                    for lock in reversed(locks_to_acquire):
                        lock.release()
        
        # No lock needed, process freely
        return await call_next(request)


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Middleware to check API key when enabled.

    Only protects OpenAI LLM endpoints.
    TEI endpoints (/v1/rerank, /v1/info) and health are excluded.
    """

    # Endpoints that require API key
    PROTECTED_PATHS = {"/v1/models", "/v1/chat/completions", "/v1/completions", "/v1/embeddings"}

    async def dispatch(self, request: Request, call_next):
        if not API_KEY_ENABLED:
            return await call_next(request)

        path = request.url.path

        # Skip API key check for TEI endpoints, health, and root
        if path in {"/health", "/v1/info", "/info", "/v1/rerank", "/rerank", "/"}:
            return await call_next(request)

        # Only check API key on protected OpenAI paths
        if not any(path.startswith(p) for p in self.PROTECTED_PATHS):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        expected_prefix = f"Bearer {LLMPROXY_API_KEY}"

        if auth_header != expected_prefix and auth_header != LLMPROXY_API_KEY:
            logger.warning(f"API key mismatch on {path}")
            return JSONResponse(
                status_code=401,
                content={"error": {"message": "Invalid API key", "type": "invalid_api_key"}}
            )

        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown lifecycle."""
    # Load and initialize global lock configuration
    load_lock_config()
    
    # Load pre/post request script hooks
    load_script_hooks()
    
    # Startup
    app.state.tei = TEIComponent()
    app.state.openai = OpenAIComponent()
    app.state.embeddings = EmbeddingsComponent()
    yield
    # Shutdown
    await app.state.tei.close()
    await app.state.openai.close()
    await app.state.embeddings.close()


app = FastAPI(
    title="LLM Proxy",
    description="Proxy server for LLM services with TEI and OpenAI compatibility",
    version="0.1.0",
    lifespan=lifespan
)

# Add middleware in order: GlobalLock first, then APIKey
app.add_middleware(GlobalLockMiddleware)
app.add_middleware(APIKeyMiddleware)


# No need for on_event decorators anymore


@app.get("/")
async def root():
    return {"service": "llmproxy", "status": "running"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


# ── TEI endpoints ──────────────────────────────────────────────────

@app.get("/info")
@app.get("/v1/info")
async def info():
    """TEI /info endpoint - returns model info from llama-server."""
    try:
        response = await app.state.tei.client.get("/v1/models")
        if response.status_code == 200:
            models = response.json()
            return {
                "model_id": models[0]["id"] if models else "unknown",
                "smart_prefix": True,
                "revision": "llama-server",
                "sha256": "unknown",
                "pool_size": 1,
                "max_concurrent_requests": 8,
                "max_client_batch_size": 128,
                "max_chunks_per_doc": 128,
                "query_max_tokens": 4096,
                "document_max_tokens": 8192,
                "num_queries": 1024,
                "num_pairs": 1024,
                "num_passages": 1024,
                "num_tokens": 1024,
                "embedding_dim": 1024,
            }
    except Exception:
        pass
    return {
        "model_id": "rerank-model",
        "smart_prefix": True,
        "revision": "llama-server",
    }


@app.post("/v1/rerank")
@app.post("/rerank")
async def rerank(request: dict):
    """TEI-compatible rerank endpoint."""
    from pydantic import TypeAdapter
    from .components.tei import RerankRequest

    import logging
    logging.info(f"LLMPROXY RERANK REQUEST: query='{request.get('query', 'N/A')[:100]}', "
                 f"model='{request.get('model', 'N/A')}', "
                 f"texts={len(request.get('texts', []))}, "
                 f"documents={len(request.get('documents', []))}, "
                 f"top_n={request.get('top_n', 'N/A')}")

    adapter = TypeAdapter(RerankRequest)
    parsed = adapter.validate_python(request)

    logging.info(f"LLMPROXY PARSED: model='{parsed.model}', query='{parsed.query[:80]}...', "
                 f"docs={len(parsed.documents) if parsed.documents else 0}")

    result = await app.state.tei.rerank(parsed)
    return result


# ── OpenAI endpoints ───────────────────────────────────────────────

@app.get("/v1/models")
async def openai_models():
    """OpenAI-compatible: list models."""
    data, status = await app.state.openai.models()
    return JSONResponse(content=data, status_code=int(status))


@app.get("/v1/models/{model_id}")
async def openai_model_detail(model_id: str):
    """OpenAI-compatible: get model detail (forward to llama-server)."""
    data, status = await app.state.openai.model_detail(model_id)
    return JSONResponse(content=data, status_code=int(status))


@app.post("/v1/chat/completions")
async def openai_chat_completions(request: dict):
    """OpenAI-compatible: chat completions (supports streaming)."""
    result, status = await app.state.openai.chat_completions(request, return_response=True)
    
    # Handle streaming response
    if isinstance(result, StreamingResponse):
        return result
    
    # Handle JSON response with proper status code
    return JSONResponse(content=result, status_code=int(status))


@app.post("/v1/completions")
async def openai_completions(request: dict):
    """OpenAI-compatible: completions (supports streaming)."""
    result, status = await app.state.openai.completions(request, return_response=True)
    
    # Handle streaming response
    if isinstance(result, StreamingResponse):
        return result
    
    # Handle JSON response with proper status code
    return JSONResponse(content=result, status_code=int(status))


@app.post("/v1/embeddings")
async def openai_embeddings(request: dict):
    """OpenAI-compatible: embeddings (uses dedicated embeddings server)."""
    data, status = await app.state.embeddings.embeddings(request, return_response=True)
    return JSONResponse(content=data, status_code=int(status))

def main():
    """Run the proxy server."""
    host = os.environ.get("LLMPROXY_HOST", "127.0.0.1")
    port = int(os.environ.get("LLMPROXY_PORT", "8000"))

    config = Config(app=app, host=host, port=port, log_level="info")
    server = Server(config=config)
    server.run()


if __name__ == "__main__":
    main()
