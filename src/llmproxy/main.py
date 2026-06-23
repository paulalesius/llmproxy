"""LLM Proxy - Multi-service proxy server."""

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from uvicorn import Config, Server
from .components.tei import TEIComponent
from .components.openai import OpenAIComponent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown lifecycle."""
    # Startup
    app.state.tei = TEIComponent()
    app.state.openai = OpenAIComponent()
    yield
    # Shutdown
    await app.state.tei.close()
    await app.state.openai.close()


app = FastAPI(
    title="LLM Proxy",
    description="Proxy server for LLM services with TEI and OpenAI compatibility",
    version="0.1.0",
    lifespan=lifespan
)


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
    """OpenAI-compatible: embeddings."""
    data, status = await app.state.openai.embeddings(request, return_response=True)
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
