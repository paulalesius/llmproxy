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
from .middleware import LoggingMiddleware, APIKeyMiddleware, GlobalLockMiddleware

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

    logger.info("All components initialized")
    yield

    logger.info("Shutting down components...")
    await app.state.openai.close()
    await app.state.tei.close()
    await app.state.embeddings.close()
    await app.state.stt.close()
    await app.state.tts.close()


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

    return app
