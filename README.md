# LLM Proxy

![Banner](banner.jpg)

**A lightweight, production-oriented FastAPI proxy that provides a unified OpenAI-compatible and TEI-compatible API surface in front of multiple specialized llama-server instances.**

## Purpose

LLM Proxy solves the common problem of having **separate backends** for different LLM capabilities:

- One llama-server for chat & completions (router mode)
- One dedicated llama-server for embeddings
- One llama-server for reranking (TEI-compatible)

Instead of clients talking to three different ports and dealing with inconsistent APIs, LLM Proxy offers a single, clean endpoint that behaves exactly like the official OpenAI and TEI APIs.

It also adds important production features that llama-server alone does not provide out of the box:
- Request serialization / global locks
- Optional API key authentication
- Pre- and post-request Python hooks
- Proper streaming, error status code forwarding, and Hindsight compatibility

## Core Features

### OpenAI Compatibility
- `GET /v1/models` and `GET /v1/models/{id}`
- `POST /v1/chat/completions` — full streaming (SSE) support
- `POST /v1/completions` — streaming + automatic `model="default"` fallback
- `POST /v1/embeddings` — routed to a **dedicated embeddings server**

### TEI Rerank Compatibility
- `POST /v1/rerank` and `POST /rerank`
- Full TEI response format with correct `index` preservation (original document positions, not re-sorted)
- Hindsight API compatibility shims:
  - `texts` → `documents`
  - `return_text` → `return_documents`
  - Automatic `model="reranker"` default

### Production-Grade Capabilities
- **Global Locks** (optional): Serialize heavy endpoints (`/v1/chat/completions`, `/v1/completions`, `/v1/embeddings`) so they never run concurrently. Prevents backend overload. Can return `503` immediately or block and wait.
- **API Key Authentication** (optional): Protect all OpenAI endpoints with a simple Bearer token.
- **Request Hooks**: Run custom Python code before and after every request (after lock acquisition).
- **Configurable Logging**: Three levels (`info`, `debug`, `trace`) with intelligent truncation of large prompts/documents.
- **Router-Mode Optimized**: Generous timeouts and graceful handling of slow model loading (20–35 s on first request to a model in router mode).
- **Accurate Error Propagation**: All backend HTTP status codes (400, 429, 500, 504, etc.) are forwarded correctly to the client.

## Architecture

```
                     ┌─────────────────────────────┐
                     │         LLM Proxy           │
                     │   (FastAPI on :8000/4001)   │
                     └──────────────┬──────────────┘
                                    │
          ┌─────────────────────────┼─────────────────────────┐
          │                         │                         │
          ▼                         ▼                         ▼
   ┌──────────────┐        ┌────────────────┐        ┌──────────────┐
   │  LLM Server  │        │ Embeddings     │        │ Reranker     │
   │  (llama.cpp) │        │ Server         │        │ Server       │
   │   :8080      │        │   :8081        │        │   :8082      │
   └──────────────┘        └────────────────┘        └──────────────┘
   chat / completions      embeddings only           /rerank only
   models
```

**Internal components**:
- `OpenAIComponent` — handles chat, completions, models + streaming logic
- `EmbeddingsComponent` — thin proxy to dedicated embeddings backend
- `TEIComponent` — rerank proxy with format normalization and index preservation
- `GlobalLockMiddleware` + `APIKeyMiddleware` + `LoggingMiddleware`

All components use `httpx.AsyncClient` with carefully tuned timeouts.

## Quick Start

```bash
cd llmproxy
uv sync

export LLMPROXY_LLM_BASE_URL=http://127.0.0.1:8080
export LLMPROXY_EMBED_BASE_URL=http://127.0.0.1:8081
export LLMPROXY_RERANK_BASE_URL=http://127.0.0.1:8082
export LLMPROXY_PORT=8000

uv run python -m src.llmproxy.main
```

## Configuration Reference

### Required Environment Variables

| Variable                              | Description                                      | Example                     |
|---------------------------------------|--------------------------------------------------|-----------------------------|
| `LLMPROXY_LLM_BASE_URL`            | Main LLM server (chat/completions/models)        | `http://127.0.0.1:8080`     |
| `LLMPROXY_EMBED_BASE_URL`     | Dedicated embeddings server                      | `http://127.0.0.1:8081`     |
| `LLMPROXY_RERANK_BASE_URL`       | Reranker / TEI server                            | `http://127.0.0.1:8082`     |

### Optional but Recommended

| Variable                        | Description                                                                 | Default     |
|---------------------------------|-----------------------------------------------------------------------------|-------------|
| `LLMPROXY_PORT`                 | Listen port                                                                 | `8000`      |
| `LLMPROXY_HOST`                 | Listen address                                                              | `0.0.0.0`   |
| `LLMPROXY_LOG_LEVEL`            | `info` / `debug` / `trace`                                                  | `info`      |
| `LLMPROXY_API_KEY`              | Enable API key protection on OpenAI endpoints when set                      | —           |
| `LLMPROXY_LOCK_CONFIG`          | Path to `config.yaml` for global locks                                      | —           |
| `LLMPROXY_LOCK_SCRIPT`          | Path to Python (.py) or shell (.sh/.bash) script executed during locked requests | —           |

### Backend Timeouts (in seconds)

| Variable                              | Description                                      | Default |
|---------------------------------------|--------------------------------------------------|---------|
| `LLMPROXY_LLM_TIMEOUT`             | Connection timeout for LLM backend               | `30`    |
| `LLMPROXY_LLM_READ_TIMEOUT`        | Read timeout for LLM backend (streaming +210s)   | `90`    |
| `LLMPROXY_RERANK_TIMEOUT`        | Connection timeout for reranker backend          | `60`    |
| `LLMPROXY_RERANK_READ_TIMEOUT`   | Read timeout for reranker backend                | `120`   |
| `LLMPROXY_EMBED_TIMEOUT`      | Connection timeout for embeddings backend        | `30`    |
| `LLMPROXY_EMBED_READ_TIMEOUT` | Read timeout for embeddings backend              | `60`    |

Connection timeout is time to establish connection. Read timeout is time to wait for response data (streaming adds extra time).

Backend-specific API keys (`LLMPROXY_LLM_API_KEY`, etc.) are also supported.

### Global Locks Example (`config.yaml`)

```yaml
global_lock:
  enabled: true
  locked_error: false          # true = return 503 immediately instead of waiting
  /v1/chat/completions:
    locks:
      - /v1/completions
      - /v1/embeddings
  /v1/embeddings:
    locks:
      - /v1/chat/completions
      - /v1/completions
  /v1/completions:
    locks:
      - /v1/chat/completions
      - /v1/embeddings
```

Endpoints not listed (`/v1/rerank`, `/v1/models`, `/health`, `/info`) always run without locks.

### Lock Script Hooks

The lock script runs during locked request execution (when global locks are enabled).

**Python scripts (.py):**
- Can define a `handle_request(request_data)` function
- `request_data` contains: `method`, `path`, `url`, `headers`
- On post-phase: also includes `response_status` and `phase="post"`
- Script runs on import if no `handle_request()` defined

**Shell scripts (.sh, .bash):**
- Must be executable (`chmod +x script.sh`)
- Request data passed as environment variables:
  - `LOCK_SCRIPT_METHOD` - HTTP method (GET, POST, etc.)
  - `LOCK_SCRIPT_PATH` - Request path (/v1/chat/completions, etc.)
  - `LOCK_SCRIPT_URL` - Full URL
  - `LOCK_SCRIPT_HEADERS` - Headers as JSON string
  - `LOCK_SCRIPT_PHASE` - "pre" (before request) or "post" (after response)
  - `LOCK_SCRIPT_RESPONSE_STATUS` - Response status code (post phase only)

**Example shell script:**
```bash
#!/bin/bash
echo "Phase: $LOCK_SCRIPT_PHASE"
echo "Path: $LOCK_SCRIPT_PATH"
if [ "$LOCK_SCRIPT_PHASE" = "post" ]; then
    echo "Response status: $LOCK_SCRIPT_RESPONSE_STATUS"
fi
```

**Example Python script:**
```python
def handle_request(request_data):
    phase = request_data.get("phase", "pre")
    if phase == "post":
        print(f"Response status: {request_data.get('response_status')}")
    print(f"Request to {request_data.get('path')}")
```

## Usage Examples

### Chat Completions (streaming)

```bash
curl -N -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.6-dense-mtp-custom",
    "messages": [{"role": "user", "content": "Write a haiku about programming."}],
    "stream": true,
    "max_tokens": 60
  }'
```

### Rerank (Hindsight-compatible format)

```bash
curl -X POST http://localhost:8000/v1/rerank \
  -H "Content-Type: application/json" \
  -d '{
    "query": "machine learning best practices",
    "texts": [
      "Always validate your models on a hold-out set.",
      "Feature engineering is often more important than model choice.",
      "The quick brown fox jumps over the lazy dog."
    ],
    "top_n": 2,
    "return_text": true
  }'
```

**Response** (note that `index` refers to the **original** position in your input array):

```json
[
  {"index": 1, "score": 0.87, "document": "Feature engineering is often more important than model choice."},
  {"index": 0, "score": 0.79, "document": "Always validate your models on a hold-out set."}
]
```

### Embeddings

```bash
curl -X POST http://localhost:8000/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "model": "bge-m3",
    "input": ["First document", "Second document"]
  }'
```

## Deployment (systemd)

A ready-to-use unit file is included (`llmproxy.service`).

Recommended setup:

```bash
sudo cp llmproxy.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now llmproxy
sudo systemctl status llmproxy
```

The service file already contains sensible defaults and runs `uv sync` on start.

## Design Philosophy & Known Behaviors

- **Transparency first**: The proxy tries hard to be invisible. Status codes, streaming format, and error shapes from the backends are preserved.
- **Router mode is first-class**: First request to an unloaded model can take 20–35 seconds. Timeouts and error handling are tuned for this.
- **Index correctness in rerank** is non-negotiable — clients must be able to map results back to their original documents.
- **No magic**: If the backend returns 500 because a model is still loading, the proxy returns 500. This is the correct and expected behavior.
- The project was built with a pragmatic, "vibe-coded but functional" approach for real internal use (RAG pipelines, Hindsight, local OpenAI-compatible clients).

## License

Apache License 2.0

---

**LLM Proxy** — One clean API in front of many specialized LLM backends.
