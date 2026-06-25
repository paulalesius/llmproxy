# LLM Proxy

![Banner](banner.jpg)

A FastAPI-based proxy server that provides OpenAI-compatible and TEI (Text Embeddings Inference) compatible endpoints for llama-server instances.

## Purpose

This proxy solves two main problems:

1. **Unified access point**: Route requests to different llama-server instances (LLM on port 8080, reranker on port 8082, embeddings on port 8081) through a single endpoint
2. **API compatibility**: Provide proper OpenAI and TEI API shapes that clients expect, with Hindsight compatibility shims

## Features

- **OpenAI-compatible endpoints**: `/v1/models`, `/v1/chat/completions`, `/v1/completions`, `/v1/embeddings`
  - Full streaming support (SSE) for chat and completions
  - Proper HTTP status code forwarding (400, 429, 500, etc.)
  - Auto-fetch default model for completions when model name is missing
  - **Dedicated embeddings server** (separate from LLM, configurable via `LLMPROXY_OAIEMBEDDINGS_BASE_URL`)

- **TEI-compatible rerank endpoint**: `/v1/rerank`, `/rerank`
  - Full TEI spec compliance with proper index preservation
  - Hindsight API compatibility (`texts` → `documents`, `return_text` → `return_documents`)
  - High timeouts (120s read) for large document batches (1500+ docs)
  - Uses `bge-reranker-v2-m3` model by default

- **Router-mode aware**: Handles llama-server's slow model loading (20-35 seconds) with appropriate timeouts

- **Global locks**: Optional serialization of chat/embeddings requests to prevent concurrent overload
  - Enabled via `LLMPROXY_GLOBAL_LOCK=1`
  - Returns `503 Service Unavailable` with `Retry-After` header when lock is held
  - Rerank, models, and health endpoints run freely without locks

- **Configurable logging**: `LLMPROXY_LOG_LEVEL` (info/debug/trace) for request/response inspection

## Vibe-Coded

This project was built for personal use with a "vibe-coded" approach — it works for the intended use cases (local RAG, Hindsight, OpenAI-compatible clients) but may not be production-grade for all edge cases. The code is functional and tested, but not exhaustively reviewed or optimized for every possible scenario.

**Use it as-is, don't over-analyze the code.** If it works for your needs, great. If you need to extend it, the architecture is clean enough to add features.


---

**Note:** This project is *vibe-coded* for personal use. The code works, but don't expect it to be pretty or fully documented. I just needed llmproxy to do its job.

---
## Installation

```bash
cd /src/llmproxy
uv sync  # or: pip install -e .
```

## Configuration

Set these environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `LLMPROXY_OAILLM_BASE_URL` | `http://127.0.0.1:8080` | LLM llama-server URL |
| `LLMPROXY_OAILLM_API_KEY` | `` | API key for LLM backend (optional) |
| `LLMPROXY_OAIEMBEDDINGS_BASE_URL` | `http://127.0.0.1:8081` | Dedicated embeddings server URL |
| `LLMPROXY_OAIEMBEDDINGS_API_KEY` | `` | API key for embeddings backend (optional) |
| `LLMPROXY_TEIRERANKER_BASE_URL` | `http://127.0.0.1:8082` | Reranker llama-server URL |
| `LLMPROXY_TEIRERANKER_API_KEY` | `` | API key for reranker backend (optional) |
| `LLMPROXY_HOST` | `0.0.0.0` | Proxy listen address |
| `LLMPROXY_PORT` | `4001` | Proxy listen port |
| `LLMPROXY_API_KEY` | `` | API key for proxy authentication (enables when set) |
| `LLMPROXY_LOG_LEVEL` | `info` | Log level: `info`, `debug`, or `trace` |
| `LLMPROXY_GLOBAL_LOCK` | `` | Enable global lock for chat/embeddings (set to "1" to enable) |

**Log levels:**
- **info**: Basic logs (endpoints, status codes, timing)
- **debug**: Full requests/responses with headers and truncated body content
- **trace**: Everything including full text content (prompts, documents, etc.)

**Global lock:**
- When enabled, serializes requests to `/v1/chat/completions` and `/v1/embeddings` endpoints
- Prevents concurrent requests from overwhelming llama-server
- Other endpoints (`/v1/rerank`, `/v1/models`, `/health`) run freely without locks
- Returns `503 Service Unavailable` with `Retry-After` header when lock is held

The systemd service defaults to `debug` level.

### Example: Enable debug logging
```bash
export LLMPROXY_LOG_LEVEL=debug
uv run python -m src.llmproxy.main
```

### Example: Enable trace logging (full text content)
```bash
export LLMPROXY_LOG_LEVEL=trace
uv run python -m src.llmproxy.main
```

### Example: Enable API key authentication
```bash
export LLMPROXY_PORT=4001
export LLMPROXY_API_KEY=min-hemliga-nyckel
uv run python -m src.llmproxy.main
```

When `LLMPROXY_PORT` and `LLMPROXY_API_KEY` are both set, the proxy requires API key authentication on all requests. Send the API key in the `Authorization` header:

- **Bearer token format**: `Authorization: Bearer min-hemliga-nyckel`
- **Raw format**: `Authorization: min-hemliga-nyckel`

Without a valid API key, requests return `401 Unauthorized`.

Debug log example:
```
2026-06-23 22:07:19,173 - src.llmproxy.components.tei - DEBUG - REQUEST [POST /rerank]: headers={}, body={"model":"reranker","query":"test query...","documents":["[20 chars]","[20 chars]"]}
2026-06-23 22:07:19,189 - src.llmproxy.components.tei - DEBUG - RESPONSE [/rerank] 200 (0.02s): body={"model":"reranker","results":[{"index":0,"score":-5.34}]}
```

## Usage

### Start the proxy

```bash
export LLMPROXY_OAILLM_BASE_URL=http://127.0.0.1:8080
export LLMPROXY_TEIRERANKER_BASE_URL=http://127.0.0.1:8082
export LLMPROXY_PORT=4001

uv run python -m src.llmproxy.main
```

Or use the systemd service:

```bash
sudo systemctl enable --now llmproxy
sudo systemctl status llmproxy
```

### OpenAI API examples

**List models:**
```bash
curl http://localhost:4001/v1/models
```

**Chat completion (non-streaming):**
```bash
curl -X POST http://localhost:4001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.6-dense-mtp-custom",
    "messages": [{"role": "user", "content": "Say hi"}],
    "max_tokens": 50
  }'
```

**Chat completion (streaming):**
```bash
curl -X POST http://localhost:4001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.6-dense-mtp-custom",
    "messages": [{"role": "user", "content": "Say hi"}],
    "stream": true
  }'
```

**Completions (auto-selects first available model):**
```bash
curl -X POST http://localhost:4001/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Once upon a time",
    "max_tokens": 20
  }'
```

**Embeddings:**
```bash
curl -X POST http://localhost:4001/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "model": "bge-m3",
    "input": "test document"
  }'
```

### TEI Rerank examples

**Rerank (TEI format with bge-reranker-v2-m3):**
```bash
curl -X POST http://localhost:4001/v1/rerank \
  -H "Content-Type: application/json" \
  -d '{
    "model": "bge-reranker-v2-m3",
    "query": "machine learning",
    "documents": ["ML is great", "ML is hard", "ML is easy"],
    "top_n": 2,
    "return_documents": true
  }'
```

**Hindsight-compatible format (simplified):**
```bash
curl -X POST http://localhost:4001/v1/rerank \
  -H "Content-Type: application/json" \
  -d '{
    "query": "machine learning",
    "texts": ["ML is great", "ML is hard"],
    "return_text": false
  }'
```

Response format:
```json
[
  {"index": 0, "score": 0.92, "document": "ML is great"},
  {"index": 2, "score": 0.78, "document": "ML is easy"}
]
```

Note: `index` preserves the original document position (not sorted position), so you can map back to your input array.

## Testing

Run integration tests:

```bash
cd /src/llmproxy
bash test.sh
```

This tests:
- Health endpoint
- TEI rerank (full and Hindsight formats with bge-reranker-v2-m3)
- OpenAI models list and detail
- Chat completions (sync and streaming)
- Completions (with auto-model selection)
- Embeddings (with bge-m3)
- Global locks (serialization, 503 responses, endpoint exclusions)

Tests are designed for router-mode llama-server, so they accept 500 (model loading) and 404 (model not loaded) as valid proxy behavior.

### API Key Authentication Tests

Run API key authentication tests:

```bash
cd /src/llmproxy
bash test_api_key.sh
```

This tests:
- Request without API key (expect 401)
- Request with wrong API key (expect 401)
- Request with correct API key as Bearer token (expect 200)
- Request with correct API key as raw value (expect 200)

## Architecture

- `src/llmproxy/main.py`: FastAPI app with route definitions
- `src/llmproxy/components/openai.py`: OpenAI endpoint proxy with streaming support (chat/completions)
- `src/llmproxy/components/embeddings.py`: Dedicated embeddings proxy (separate from LLM server)
- `src/llmproxy/components/tei.py`: TEI rerank proxy with Hindsight compatibility

All components use `httpx.AsyncClient` with configurable timeouts, proper error handling, and logging based on `LLMPROXY_LOG_LEVEL`.

## Known behaviors

- **Router-mode model loading**: First request to an unloaded model can take 20-35 seconds. Subsequent requests are fast.
- **404 on `/v1/models/{id}`**: llama-server router mode returns 404 for individual model queries if the model isn't loaded. The proxy forwards this correctly.
- **500 on completions/embeddings**: May occur if the model needs to load. The proxy forwards the error with proper status code.
- **Index preservation**: TEI rerank results preserve original document indices, not sorted positions.

## License

MIT
