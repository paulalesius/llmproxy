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
- Request serialization / global locks (backend-based, not path-based)
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
- **CLI Configuration**: Use `-c/--config` flag to specify config file path (replaces `LLMPROXY_CONFIG` env var)

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
- `Backend` (enum) — LLM, EMBED, RERANK with path mappings
- `GlobalLockMiddleware` + `APIKeyMiddleware` + `LoggingMiddleware`

All components use `httpx.AsyncClient` with carefully tuned timeouts.

## Key Implementation Details

### Backend-based global locking

Each backend has its own lock. When a request hits a path, `GlobalLockMiddleware`:
1. Identifies the backend for the path using `get_backend_for_path()`
2. Acquires locks for all backends configured in `config.yaml`
3. Executes the request
4. Releases locks

**Critical:** Backends do NOT lock themselves. If `llm` locks `embed` and `rerank`, it does NOT lock `llm`.

### Dynamic path matching

`get_backend_for_path()` uses prefix matching for dynamic paths:

```python
if path.startswith("/v1/models/") and path != "/v1/models":
    return Backend.LLM
```

This ensures `/v1/models/xxx` is matched to LLM backend for locking.

### Tuple handling

All component methods return `(body, status)` tuple when `return_response=True`:

```python
# Correct (all endpoints)
result, status = await app.state.llm.chat_completions(request, return_response=True)
return JSONResponse(content=result, status_code=int(status))

# Wrong (old embeddings code)
result, status = await app.state.embeddings.embeddings(request)  # Missing return_response=True
```

### Streaming support

Detects `stream: true` in request body, returns `StreamingResponse(resp.aiter_lines(), media_type="text/event-stream")`

### Error forwarding

All endpoints return `(body, status)` tuple, `main.py` wraps with `JSONResponse(content=body, status_code=status)`

### Index preservation

TEI rerank results: `index = item.get("index", i)` preserves backend's original document index, not sorted position

### Hindsight compatibility

- `texts` → `documents`
- `return_text` → `return_documents`
- Auto-default `model="reranker"` if missing
- Auto-default `documents=["no documents provided"]` if empty

## Quick Start

```bash
cd llmproxy
uv sync

export LLMPROXY_LLM_BASE_URL=http://127.0.0.1:8080
export LLMPROXY_EMBED_BASE_URL=http://127.0.0.1:8081
export LLMPROXY_RERANK_BASE_URL=http://127.0.0.1:8082
export LLMPROXY_PORT=8000

uv run python -m src.llmproxy.main -c /path/to/config.yaml
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
| `-c, --config PATH`             | Path to `config.yaml` for global locks and main configuration               | —           |
| `LLMPROXY_LOCK_SCRIPT`          | Python (.py) / Shell (.sh/.bash) / Bash command executed during locked requests | —           |

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

### Global Locks Example (backend-based, `config.yaml`)

Backend-based locking is the recommended approach. Each backend configures which OTHER backends it locks:

```yaml
server:
  host: 0.0.0.0
  port: 4001

# Optional global lock configuration
# Remove this section entirely to disable locking
global_lock:
  enabled: true
  locked_error: false
  lock_script: ""

backends:
  llm:
    base_url: http://127.0.0.1:8080
    locks:
      - embed
      - rerank
  embed:
    base_url: http://127.0.0.1:8081
    locks:
      - llm
      - rerank
  rerank:
    base_url: http://127.0.0.1:8082
    locks:
      - llm
      - embed
```

**Available backends:**
- `llm`: `/v1/chat/completions`, `/v1/completions`, `/v1/models`, `/v1/models/{id}`
- `embed`: `/v1/embeddings`
- `rerank`: `/v1/rerank`, `/rerank`, `/v1/info`, `/info`

**Key points:**
- Backends do NOT lock themselves (no `llm` in `llm.locks`)
- Each backend independently configures which other backends to lock
- Dynamic paths like `/v1/models/xxx` are automatically matched to their backend
- When `/v1/chat/completions` (LLM) runs, it locks `embed` and `rerank` backends
- **`global_lock` section is optional** — omit it entirely to disable locking
- **`enabled: true`** — if section exists but `enabled: false`, locking is disabled
- **`locked_error: false`** — if `true`, returns 503 immediately when lock is held; if `false`, blocks until lock acquired
- **`lock_script: ""`** — optional Python/shell/bash command to run during locked execution

**Legacy path-based locking** (older format, still supported):

```yaml
global_lock:
  enabled: true
  /v1/chat/completions:
    locks:
      - /v1/completions
      - /v1/embeddings
```

Path-based locking is automatically mapped to backends, but backend-based is cleaner and easier to maintain.

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

**Bash commands (raw command string):**
- If `LLMPROXY_LOCK_SCRIPT` is not a file path, it's treated as a raw bash command
- Same environment variables as shell scripts
- Example: `LLMPROXY_LOCK_SCRIPT="echo 'Lock acquired' >> /var/log/llmproxy.lock"`

**Mode detection:**
1. If path ends with `.py` → Python script
2. If path ends with `.sh` or `.bash` → Shell script
3. If path is a file with other extension → Shell script
4. If path is not a file → Bash command


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

The service file needs to be updated to use `-c` flag instead of `LLMPROXY_CONFIG` env var.

## Design Philosophy & Known Behaviors

- **Transparency first**: The proxy tries hard to be invisible. Status codes, streaming format, and error shapes from the backends are preserved.
- **Router mode is first-class**: First request to an unloaded model can take 20–35 seconds. Timeouts and error handling are tuned for this.
- **Index correctness in rerank** is non-negotiable — clients must be able to map results back to their original documents.
- **No magic**: If the backend returns 500 because a model is still loading, the proxy returns 500. This is the correct and expected behavior.
- The project was built with a pragmatic, "vibe-coded but functional" approach for real internal use (RAG pipelines, Hindsight, local OpenAI-compatible clients).

## Troubleshooting

### 500 Internal Server Error on embeddings

**Cause**: `openai_embeddings()` endpoint unpacking bug (old code)

**Solution**: Ensure `openai_embeddings()` uses `return_response=True`:

```python
result, status = await app.state.embeddings.embeddings(request, return_response=True)
return JSONResponse(content=result, status_code=int(status))
```

### Global locks not working

1. Check `config.yaml` has `global_lock.enabled: true`
2. Verify backend names are correct (`llm`, `embed`, `rerank`)
3. Ensure backends don't lock themselves (no `llm` in `llm.locks`)
4. Check `/v1/models/xxx` paths are matched (prefix matching in `backend.py`)
5. Pass config file with `-c /path/to/config.yaml` flag

### uv location

`uv` is in `~/.local/bin/uv` (or `~/.cargo/bin/uv` if installed via cargo). Add to PATH or use full path:

```bash
~/.local/bin/uv run python -m src.llmproxy.main -c /path/to/config.yaml
```

### Dynamic paths not matched for locking

**Cause**: `/v1/models/xxx` paths not matched by `get_backend_for_path()`

**Solution**: Ensure `backend.py` has prefix matching:

```python
if path.startswith("/v1/models/") and path != "/v1/models":
    return Backend.LLM
```

## References

## References

- [SKILL.md](./SKILL.md) - Setup guide and common pitfalls
- [test.sh](./test.sh) - Integration tests (run with `uv run pytest`)
- [llmproxy.service](./llmproxy.service) - systemd unit file
- [src/llmproxy/config.yaml](./src/llmproxy/config.yaml) - Backend-based lock configuration

## Testing

Run integration tests:

```bash
cd /src/llmproxy
uv run python -m src.llmproxy.main -c /path/to/config.yaml
```

Expected: All 13 global lock tests pass (backend servers must be running)

Full test suite:

```bash
cd /src/llmproxy
uv run python -m src.llmproxy.main -c /path/to/config.yaml
```

Expected: 38/38 tests pass (backend servers must be running)

Apache License 2.0

---

**LLM Proxy** — One clean API in front of many specialized LLM backends.
