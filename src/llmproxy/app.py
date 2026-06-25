"""FastAPI application factory - Clean working version."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .config import get_config, reload_config
from .components.openai import OpenAIComponent
from .components.tei import TEIComponent
from .components.embeddings import EmbeddingsComponent
from .middleware import LoggingMiddleware, APIKeyMiddleware, GlobalLockMiddleware

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = get_config()
    logger.info("Starting LLM Proxy components...")

    app.state.openai = OpenAIComponent()
    app.state.tei = TEIComponent()
    app.state.embeddings = EmbeddingsComponent()

    logger.info("All components initialized")
    yield

    logger.info("Shutting down components...")
    await app.state.openai.close()
    await app.state.tei.close()
    await app.state.embeddings.close()


def create_app(config_path: str | None = None) -> FastAPI:
    if config_path:
        reload_config(config_path)

    config = get_config()

    app = FastAPI(
        title="LLM Proxy",
        version="0.3.0",
        lifespan=lifespan,
    )

    # Middleware
    app.add_middleware(LoggingMiddleware)
    if config.api_key:
        app.add_middleware(APIKeyMiddleware)
    if config.lock.enabled:
        app.add_middleware(GlobalLockMiddleware)

    # ==================== ROUTES USING MODERN COMPONENTS ====================

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        body = await request.json()
        # Always returns tuple (data, status) now
        data, status = await app.state.openai.chat_completions(body)
        
        # Streaming case: component already returns a StreamingResponse
        if isinstance(data, StreamingResponse):
            return data
        return JSONResponse(content=data, status_code=status)


    @app.post("/v1/completions")
    async def completions(request: Request):
        body = await request.json()
        # Always returns tuple (data, status) now
        data, status = await app.state.openai.completions(body)
        
        if isinstance(data, StreamingResponse):
            return data
        return JSONResponse(content=data, status_code=status)

    @app.post("/v1/embeddings")
    async def embeddings(request: Request):
        body = await request.json()
        data, status = await app.state.embeddings.embeddings(body, return_response=True)
        return JSONResponse(content=data, status_code=status)

    @app.post("/v1/rerank")
    async def rerank(request: Request):
        from .components.tei import RerankRequest
        body = await request.json()
        req = RerankRequest(**body)
        result = await app.state.tei.rerank(req)
        # Return just the results list (TEI-compatible format)
        return result.results

    @app.post("/rerank")
    async def rerank_alt(request: Request):
        """Alternative /rerank path (same as /v1/rerank)."""
        from .components.tei import RerankRequest
        body = await request.json()
        req = RerankRequest(**body)
        result = await app.state.tei.rerank(req)
        return result.results

    @app.get("/v1/models")
    async def list_models():
        data, status = await app.state.openai.models()
        return JSONResponse(content=data, status_code=status)

    @app.get("/")
    async def root():
        return {"service": "llmproxy", "status": "running"}

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    # TEI info endpoints
    @app.get("/info")
    async def info():
        """TEI-compatible info endpoint (proxied to rerank backend /info)."""
        try:
            resp = await app.state.tei.client.get("/info")
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return {"model_id": "reranker", "revision": "unknown", "task": "reranking"}

    @app.get("/v1/info")
    async def info_v1():
        """Alternative /v1/info path (same as /info)."""
        return await info()

    return app
