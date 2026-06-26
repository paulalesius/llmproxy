"""FastAPI application factory - Clean working version."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.responses import Response as StarletteResponse

from .config import get_config, reload_config
from .components.openai import OpenAIComponent
from .components.tei import TEIComponent
from .components.embeddings import EmbeddingsComponent
from .components.audio import STTComponent, TTSComponent
from .components.forward import ForwardComponent
from .middleware import LoggingMiddleware, APIKeyMiddleware, GlobalLockMiddleware
from .routing.backends import get_backend_for_path

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = get_config()
    logger.info("Starting LLM Proxy components...")

    app.state.openai = OpenAIComponent()
    app.state.tei = TEIComponent()
    app.state.embeddings = EmbeddingsComponent()
    app.state.stt = STTComponent()
    app.state.tts = TTSComponent()

    # Custom forward backends (transparent proxy + locking participants)
    app.state.forwarders: dict[str, ForwardComponent] = {}
    for name, backend_cfg in config.backends.items():
        if backend_cfg.type == "forward" and backend_cfg.enabled:
            fc = ForwardComponent(
                name=name,
                url=backend_cfg.url,
                path_prefix=backend_cfg.path_prefix,
                strip_prefix=backend_cfg.strip_prefix,
                timeout=backend_cfg.timeout,
                read_timeout=backend_cfg.read_timeout,
                api_key=backend_cfg.api_key,
            )
            app.state.forwarders[name] = fc
            logger.info(f"Registered custom forwarder: {name} (prefix={backend_cfg.path_prefix})")

    logger.info("All components initialized")
    yield

    logger.info("Shutting down components...")
    await app.state.openai.close()
    await app.state.tei.close()
    await app.state.embeddings.close()
    await app.state.stt.close()
    await app.state.tts.close()
    for fc in app.state.forwarders.values():
        await fc.close()


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
    if config.lock.enabled:
        app.add_middleware(GlobalLockMiddleware)
    if config.api_key:
        app.add_middleware(APIKeyMiddleware)

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
        data, status = await app.state.embeddings.embeddings(body)
        return JSONResponse(content=data, status_code=status)

    @app.post("/v1/rerank")
    async def rerank(request: Request):
        from .components.tei import RerankRequest
        body = await request.json()
        req = RerankRequest(**body)
        data, status = await app.state.tei.rerank(req)
        # Return just the results list (TEI-compatible format)
        return JSONResponse(content=data.get("results", []), status_code=status)

    @app.post("/rerank")
    async def rerank_alt(request: Request):
        """Alternative /rerank path (same as /v1/rerank)."""
        from .components.tei import RerankRequest
        body = await request.json()
        req = RerankRequest(**body)
        data, status = await app.state.tei.rerank(req)
        # Return just the results list (TEI-compatible format)
        return JSONResponse(content=data.get("results", []), status_code=status)

    @app.get("/v1/models")
    async def list_models():
        data, status = await app.state.openai.models()
        return JSONResponse(content=data, status_code=status)

    @app.get("/v1/models/{model_id}")
    async def model_detail(model_id: str):
        data, status = await app.state.openai.model_detail(model_id)
        return JSONResponse(content=data, status_code=status)

    @app.get("/models/{model_id}")
    async def model_detail_legacy(model_id: str):
        """Legacy /models/{id} path (same as /v1/models/{id})."""
        data, status = await app.state.openai.model_detail(model_id)
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
        data, status = await app.state.tei.get_info()
        return JSONResponse(content=data, status_code=status)

    @app.get("/v1/info")
    async def info_v1():
        """Alternative /v1/info path (same as /info)."""
        data, status = await app.state.tei.get_info()
        return JSONResponse(content=data, status_code=status)

    # ==================== AUDIO (STT / TTS) ROUTES ====================

    @app.post("/v1/audio/transcriptions")
    async def audio_transcriptions(request: Request):
        """OpenAI-compatible STT transcription endpoint (multipart/form-data)."""
        resp = await app.state.stt.transcriptions(request)
        return StarletteResponse(
            content=resp.content,
            status_code=resp.status_code,
            headers=dict(resp.headers),
            media_type=resp.headers.get("content-type", "application/json"),
        )

    @app.post("/v1/audio/translations")
    async def audio_translations(request: Request):
        """OpenAI-compatible STT translation endpoint (multipart/form-data)."""
        resp = await app.state.stt.translations(request)
        return StarletteResponse(
            content=resp.content,
            status_code=resp.status_code,
            headers=dict(resp.headers),
            media_type=resp.headers.get("content-type", "application/json"),
        )

    @app.post("/v1/audio/speech")
    async def audio_speech(request: Request):
        """OpenAI-compatible TTS endpoint (JSON in, audio binary out)."""
        resp = await app.state.tts.speech(request)
        # Determine media type from backend or default to audio/mpeg
        media_type = resp.headers.get("content-type", "audio/mpeg")
        return StarletteResponse(
            content=resp.content,
            status_code=resp.status_code,
            headers=dict(resp.headers),
            media_type=media_type,
        )

    @app.get("/debug/routes")
    async def debug_list_routes():
        """Lists all registered routes - for debugging only."""
        routes = []
        for route in app.routes:
            methods = getattr(route, "methods", None)
            routes.append({
                "path": getattr(route, "path", str(route)),
                "methods": list(methods) if methods else None,
                "name": getattr(route, "name", None),
            })
        return {"count": len(routes), "routes": routes}

    # ============================================================
    # CATCH-ALL FOR CUSTOM FORWARD BACKENDS (only if any are configured)
    # Registered last so core routes always take precedence.
    # ============================================================
    has_custom_forwarders = any(
        getattr(bcfg, "type", "core") == "forward"
        for bcfg in config.backends.values()
    )

    if has_custom_forwarders:
        @app.api_route(
            "/{full_path:path}",
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
        )
        async def custom_forward_catch_all(request: Request, full_path: str):
            """Catch-all route for custom forward backends.

            Only registered when at least one custom forwarder is configured
            in backends.custom. This guarantees zero impact on existing
            core routes (audio, chat, embeddings, etc.) when the feature is not used.
            """
            path = f"/{full_path}" if full_path else "/"

            # Core routes take precedence (registered first + get_backend_for_path checks them first).
            # Only forward if it's a custom backend (str name).
            backend = get_backend_for_path(path)

            if isinstance(backend, str) and backend in getattr(app.state, "forwarders", {}):
                comp: ForwardComponent = app.state.forwarders[backend]
                return await comp.forward(request)

            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Not found")

    return app
