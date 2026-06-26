"""Shared fixtures for llmproxy tests - Mocked by default for reliability."""

import pytest
import httpx
import respx
import re
from fastapi.testclient import TestClient
from pathlib import Path
import json


# ============================================================
# MOCKED APP (DEFAULT - Recommended)
# ============================================================

# Create a session-scoped respx mock
mock_router = respx.MockRouter(assert_all_called=False)

# Configure all the mock routes
@mock_router.post("http://127.0.0.1:8080/v1/chat/completions")
async def mock_chat(request):
    # Read content and parse JSON
    try:
        content = await request.aread()
        body = json.loads(content) if content else {}
    except (json.JSONDecodeError, Exception):
        body = {}
    is_stream = body.get("stream", False)
    
    if is_stream:
        # Return streaming response with proper SSE format (double newlines between events)
        content_str = (
            'data: {"id": "chatcmpl-mock","object": "chat.completion.chunk","choices": [{"index": 0,"delta": {"role": "assistant","content": "Mocked response"},"finish_reason": null}]}\\n\\n'
            "data: [DONE]\\n\\n"
        )
        return httpx.Response(
            200,
            content=content_str,
            headers={"Content-Type": "text/event-stream"}
        )
    else:
        return httpx.Response(200, json={
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "created": 1234567890,
            "model": body.get("model", "mock-model"),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Mocked response"
                    },
                    "finish_reason": "stop"
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        })

@mock_router.post("http://127.0.0.1:8080/v1/completions")
async def mock_completions(request):
    try:
        content = await request.aread()
        body = json.loads(content) if content else {}
    except (json.JSONDecodeError, Exception):
        body = {}
    is_stream = body.get("stream", False)
    
    if is_stream:
        content_str = (
            'data: {"id": "cmpl-mock","object": "text_completion.chunk","choices": [{"text": "Mocked completion","finish_reason": null}]}\\n\\n'
            "data: [DONE]\\n\\n"
        )
        return httpx.Response(
            200,
            content=content_str,
            headers={"Content-Type": "text/event-stream"}
        )
    else:
        return httpx.Response(200, json={
            "id": "cmpl-mock",
            "object": "text_completion",
            "created": 1234567890,
            "model": body.get("model", "default"),
            "choices": [
                {
                    "text": "Mocked completion",
                    "index": 0,
                    "finish_reason": "stop"
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}
        })

@mock_router.get("http://127.0.0.1:8080/v1/models")
async def mock_models_list(request):
    return httpx.Response(200, json={
        "object": "list",
        "data": [
            {"id": "qwen3.6-dense-mtp-custom", "object": "model", "owned_by": "llama.cpp"},
            {"id": "bge-m3", "object": "model", "owned_by": "llama.cpp"},
            {"id": "reranker", "object": "model", "owned_by": "llama.cpp"}
        ]
    })

@mock_router.get(re.compile(r"http://127.0.0.1:8080/v1/models/([^/]+)"))
async def mock_model_detail(request):
    # Catch-all for /v1/models/{model_id} paths - extract model_id from URL
    # Path is /v1/models/{model_id} -> split gives ['', 'v1', 'models', 'model_id']
    path_parts = request.url.path.rstrip("/").split("/")
    model_id = path_parts[-1] if len(path_parts) >= 4 and path_parts[-1] else "unknown"
    return httpx.Response(200, json={
        "id": model_id,
        "object": "model",
        "owned_by": "llama.cpp"
    })

@mock_router.post("http://127.0.0.1:8081/v1/embeddings")
async def mock_embeddings(request):
    # Safely parse request body
    try:
        content = await request.aread()
        body = json.loads(content) if content else {}
    except (json.JSONDecodeError, Exception):
        body = {}
    input_text = body.get("input", "")
    
    # Handle both string and list input
    if isinstance(input_text, str):
        inputs = [input_text]
    elif isinstance(input_text, list):
        inputs = input_text
    else:
        inputs = []
    
    embeddings = []
    for i, text in enumerate(inputs):
        # Generate a deterministic "embedding" based on text length
        emb = [float(len(text) - j % 100) for j in range(1024)]
        embeddings.append({
            "object": "embedding",
            "embedding": emb,
            "index": i
        })
    
    return httpx.Response(200, json={
        "object": "list",
        "data": embeddings,
        "model": body.get("model", "bge-m3"),
        "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)}
    })

@mock_router.post("http://127.0.0.1:8082/rerank")
async def mock_rerank(request):
    # Safely parse request body
    try:
        content = await request.aread()
        body = json.loads(content) if content else {}
    except (json.JSONDecodeError, Exception):
        body = {}
    query = body.get("query", "")
    documents = body.get("documents", body.get("texts", []))
    top_n = body.get("top_n", len(documents))
    return_docs = body.get("return_documents", body.get("return_text", False))
    
    # Generate scores based on query-document overlap
    results = []
    for i, doc in enumerate(documents):
        # Simple scoring: count common words, with fallback score
        query_words = set(query.lower().split())
        doc_words = set(doc.lower().split())
        overlap = len(query_words & doc_words)
        # Give a base score of 0.3 plus overlap bonus
        score = 0.3 + (overlap * 0.2) if query_words else 0.3
        score = min(score, 1.0)  # Cap at 1.0
        
        result = {
            "index": i,
            "score": round(score, 4)
        }
        if return_docs:
            result["document"] = doc
        
        results.append(result)
    
    # Sort by score descending and take top_n
    results.sort(key=lambda x: x["score"], reverse=True)
    results = results[:top_n]
    
    return httpx.Response(200, json={
        "model": body.get("model", "reranker"),
        "results": results
    })

@mock_router.post("http://127.0.0.1:8082/v1/rerank")
async def mock_rerank_v1(request):
    return await mock_rerank(request)

@mock_router.get("http://127.0.0.1:8082/info")
async def mock_info(request):
    return httpx.Response(200, json={
        "model_id": "bge-reranker-v2-m3",
        "revision": "1.2.3",
        "task": "reranking"
    })

@mock_router.get("http://127.0.0.1:8082/v1/info")
async def mock_info_v1(request):
    return await mock_info(request)


@pytest.fixture(scope="session")
def app():
    """FastAPI app with all backends mocked. Used by default."""
    from src.llmproxy.app import create_app
    
    # Start the mock router
    mock_router.start()
    try:
        app = create_app()
        yield app
    finally:
        mock_router.stop()


@pytest.fixture
def sync_client(app):
    """Sync client that uses the mocked app by default."""
    with TestClient(app) as client:
        yield client


# ============================================================
# Real server (only when you explicitly want live backends)
# ============================================================

@pytest.fixture(scope="session")
def llmproxy_server():
    """Real server with real backends. Only use when needed."""
    import os
    import subprocess
    import time

    project_root = Path(__file__).parent.parent
    config_path = project_root / "config" / "config.test.yaml"

    proc = subprocess.Popen(
        ["python3", "-m", "src.llmproxy.main", "-c", str(config_path)],
        cwd=str(project_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    base_url = "http://127.0.0.1:4002"
    for _ in range(15):
        try:
            if httpx.get(f"{base_url}/health", timeout=2).status_code == 200:
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        proc.terminate()
        pytest.fail("Real server failed to start")

    yield base_url
    proc.terminate()
