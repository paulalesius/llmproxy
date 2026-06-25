"""LLM Proxy - Multi-service proxy server."""

import os
import sys
import argparse
import logging
import asyncio
from contextlib import asynccontextmanager
from typing import Dict, Optional
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware
from uvicorn import Config, Server
import yaml
from .components.tei import TEIComponent
from .backend import Backend, BACKEND_NAME_TO_ENUM, get_backend_for_path
from .components.embeddings import EmbeddingsComponent
from .components.openai import OpenAIComponent
from .script_loader import load_lock_script, execute_lock_script
from .logging_middleware import LoggingMiddleware


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="LLM Proxy - Multi-service proxy server"
    )
    parser.add_argument(
        "-c", "--config",
        type=str,
        default=None,
        help="Path to config.yaml for global locks and main configuration"
    )
    return parser.parse_args()

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
# Disabled by default unless explicitly enabled via global_lock.enabled in config file
# Use -c/--config flag to specify config path
args = parse_args()
LOCK_CONFIG_PATH = args.config

# Lock script - single script that runs during locked request execution
# Can be:
#   1. Python script (.py) - loads as module with handle_request()
#   2. Shell script (.sh, .bash) - loads as executable script
#   3. Bash command - raw command string (if not a file)
LOCK_SCRIPT_PATH = os.environ.get("LLMPROXY_LOCK_SCRIPT", "")

# Lock state - initialized in lifespan
# Backend-based locking: one lock per backend, not per path
lock_config: Optional[dict] = None
backend_locks: Dict[Backend, asyncio.Lock] = {}

# Script hooks - initialized in lifespan
lock_script_hook: Optional[dict] = None


def load_lock_config():
    """Load global lock configuration from YAML file.
    
    Backend-based locking: each backend lists which OTHER backends to lock.
    Example config:
      llm:
        locks:
          - embed
          - rerank
      embed:
        locks:
          - llm
          - rerank
      rerank:
        locks:
          - llm
          - embed
    """
    global lock_config, backend_locks
    
    if not LOCK_CONFIG_PATH:
        logger.info("Global lock disabled (-c/--config not set)")
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
        
        if lock_config is None:
            logger.info("Global lock: no config found, disabled by default")
            lock_config = {"enabled": False}
            return
        
        if not lock_config.get("enabled", False):
            logger.info("Global lock: config found but not enabled")
            return
        
        # Create one lock per backend
        backend_locks = {backend: asyncio.Lock() for backend in Backend}
        
        # Parse backend-based configuration
        # Each backend lists which OTHER backends it should lock
        backend_lock_mapping: dict[Backend, set[Backend]] = {}
        
        for backend_name, config_entry in lock_config.items():
            if backend_name == "enabled":
                continue
            
            # Get Backend enum from string name
            if backend_name not in BACKEND_NAME_TO_ENUM:
                logger.warning(f"Unknown backend in config: {backend_name}")
                continue
            
            backend_enum = BACKEND_NAME_TO_ENUM[backend_name]
            locks_to_acquire: set[Backend] = set()
            
            if isinstance(config_entry, dict):
                for lock_item in config_entry.get("locks", []):
                    if lock_item in BACKEND_NAME_TO_ENUM:
                        locks_to_acquire.add(BACKEND_NAME_TO_ENUM[lock_item])
                    else:
                        logger.warning(f"Unknown lock for {backend_name}: {lock_item}")
            
            elif isinstance(config_entry, list):
                for lock_item in config_entry:
                    if lock_item in BACKEND_NAME_TO_ENUM:
                        locks_to_acquire.add(BACKEND_NAME_TO_ENUM[lock_item])
            
            # Validate: backend should not lock itself
            if backend_enum in locks_to_acquire:
                logger.warning(f"Backend {backend_name} is configured to lock itself - this may cause deadlock")
                locks_to_acquire.discard(backend_enum)
            
            if locks_to_acquire:
                backend_lock_mapping[backend_enum] = locks_to_acquire
                lock_names = [b.value for b in locks_to_acquire]
                logger.info(f"Backend {backend_name} locks: {lock_names}")
            else:
                logger.info(f"Backend {backend_name} has no locks configured")
        
        # Store the mapping in lock_config for middleware to use
        lock_config["backend_locks"] = backend_lock_mapping
        
        logger.info(f"Global lock enabled with {len(backend_locks)} backend locks")
        
    except Exception as e:
        logger.error(f"Failed to load lock config: {e}")


def load_lock_script():
    """Load lock script hook (Python, shell script, or bash command)."""
    global lock_script_hook
    
    if not LOCK_SCRIPT_PATH:
        lock_script_hook = None
        logger.info("Lock script disabled (LLMPROXY_LOCK_SCRIPT not set)")
        return
    
    # Import the loader function to avoid name collision
    from .script_loader import load_lock_script as load_script_from_path
    
    # Use the new load_lock_script() function that handles all three modes
    hook = load_script_from_path(LOCK_SCRIPT_PATH)
    lock_script_hook = hook
    
    if hook["error"]:
        logger.warning(f"Lock script: {hook['error']}")
    else:
        if hook["type"] == "python":
            logger.info(f"Lock script loaded (Python): {LOCK_SCRIPT_PATH}")
            if hook["handle_request"]:
                logger.info("  - has handle_request() function")
            else:
                logger.info("  - runs as plain script on import")
        elif hook["type"] == "shell":
            logger.info(f"Lock script loaded (shell): {hook['path']}")
            logger.info(f"  - executable: {hook['executable']}")
        elif hook["type"] == "command":
            logger.info(f"Lock script loaded (bash command): {hook['command']}")


class GlobalLockMiddleware(BaseHTTPMiddleware):
    """Middleware that applies global locks based on backend configuration.
    
    Backend-based locking: each backend has one shared lock.
    When a path is accessed, the backend it belongs to acquires locks
    for all OTHER backends configured in lock_config.
    
    Example: if /v1/chat/completions is called:
    1. Path belongs to LLM backend
    2. LLM backend is configured to lock [embed, rerank]
    3. Acquire locks for embed and rerank backends
    4. Execute request
    5. Release locks
    """
    
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        
        if lock_config and lock_config.get("enabled"):
            # Get the backend for this path
            path_backend = get_backend_for_path(path)
            
            if path_backend:
                # Get which backends this backend should lock
                backend_locks_mapping = lock_config.get("backend_locks", {})
                locks_to_acquire_backends = backend_locks_mapping.get(path_backend, set())
                
                if locks_to_acquire_backends:
                    # Convert to actual lock objects
                    locks_to_acquire = []
                    for lock_backend in sorted(locks_to_acquire_backends, key=lambda b: b.value):
                        if lock_backend in backend_locks:
                            locks_to_acquire.append(backend_locks[lock_backend])
                    
                    if locks_to_acquire:
                        logger.info(f"[GlobalLock] {path} ({path_backend.value}) acquiring locks for: {[b.value for b in locks_to_acquire_backends]}")
                        
                        locked_error = lock_config.get("locked_error", False)
                        
                        if locked_error:
                            # Best-effort check: om någon av locks är upptagen → 503 direkt
                            if any(lock.locked() for lock in locks_to_acquire):
                                return JSONResponse(
                                    status_code=503,
                                    content={
                                        "error": {
                                            "message": f"Service temporarily busy, {path_backend.value} backend is locked",
                                            "type": "service_busy",
                                            "retry_after": 2
                                        }
                                    }
                                )
                            
                            # Alla var lediga → lås dem
                            for lock in locks_to_acquire:
                                await lock.acquire()
                        else:
                            # Block until all locks are acquired
                            for lock in locks_to_acquire:
                                await lock.acquire()
                        
                        try:
                            # Lock script hook (runs once during locked execution)
                            if lock_script_hook:
                                request_data = {
                                    "method": request.method,
                                    "path": request.url.path,
                                    "url": str(request.url),
                                    "headers": dict(request.headers),
                                }
                                result = execute_lock_script(lock_script_hook, request_data)
                                if not result["success"]:
                                    logger.warning(f"Lock script failed: {result['error']}")
                            
                            response = await call_next(request)
                            
                            # Post-response hook (if Python with handle_request that accepts response_status)
                            if lock_script_hook and lock_script_hook.get("type") == "python":
                                request_data = {
                                    "method": request.method,
                                    "path": request.url.path,
                                    "url": str(request.url),
                                    "headers": dict(request.headers),
                                    "response_status": response.status_code,
                                }
                                if lock_script_hook.get("handle_request"):
                                    result = execute_lock_script(lock_script_hook, request_data)
                                    if not result["success"]:
                                        logger.warning(f"Lock script post-response failed: {result['error']}")
                            
                            return response
                            
                        finally:
                            # Release locks in reverse order
                            for lock in reversed(locks_to_acquire):
                                lock.release()
            
            # No locks configured for this path/backend
            return await call_next(request)
        
        # Global lock disabled
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
    
    # Load lock script hook
    load_lock_script()
    
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
app.add_middleware(LoggingMiddleware)


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
    """TEI /info endpoint - returns static model info."""
    return {
        "model_id": "rerank-model",
        "smart_prefix": True,
        "revision": "llama-server",
        "pool_size": 1,
        "max_concurrent_requests": 8,
        "max_client_batch_size": 128,
        "max_chunks_per_doc": 128,
        "num_queries": 1024,
        "num_pairs": 1024,
        "num_passages": 1024,
        "num_tokens": 1024,
        "embedding_dim": 1024,
    }


@app.post("/v1/rerank")
@app.post("/rerank")
async def rerank(request: dict):
    """TEI-compatible rerank endpoint."""
    from pydantic import TypeAdapter
    from .components.tei import RerankRequest

    logger.info(f"LLMPROXY RERANK REQUEST: query='{request.get('query', 'N/A')[:100]}', "
                 f"model='{request.get('model', 'N/A')}', "
                 f"texts={len(request.get('texts', []))}, "
                 f"documents={len(request.get('documents', []))}, "
                 f"top_n={request.get('top_n', 'N/A')}")

    adapter = TypeAdapter(RerankRequest)
    parsed = adapter.validate_python(request)

    logger.info(f"LLMPROXY PARSED: model='{parsed.model}', query='{parsed.query[:80]}...', "
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
    """OpenAI-compatible: embeddings endpoint."""
    result, status = await app.state.embeddings.embeddings(request, return_response=True)
    return JSONResponse(content=result, status_code=int(status))


if __name__ == "__main__":
    import uvicorn
    
    port = int(os.environ.get("LLMPROXY_PORT", 4001))
    uvicorn.run(app, host="0.0.0.0", port=port)
