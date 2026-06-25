---
category: devops
aliases:
  - llmproxy-setup
  - llm-proxy-configuration
  - llama-server-proxy
---

# LLM Proxy Setup and Configuration

Set up and configure the LLM Proxy server that provides OpenAI-compatible and TEI-compatible endpoints for llama-server instances.

## Overview

The LLM Proxy is a FastAPI-based server that:
- Routes requests to different llama-server backends (LLM + reranker + embeddings)
- Provides OpenAI API compatibility (models, chat/completions, embeddings, streaming)
- Provides TEI rerank compatibility with Hindsight API shims
- Handles llama-server router-mode quirks (slow model loading, 404 on unloaded models)
- Backend-based global locking (configure which backends lock each other)

## Quick Start

```bash
cd /src/llmproxy

# Edit config.yaml or use your own config file
uv run python -m src.llmproxy.main -c /path/to/config.yaml
```

**uv location:** `~/.local/bin/uv` or `~/.cargo/bin/uv` (if installed via cargo)

Configuration is done entirely through YAML config files. No environment variables are required.

## Backend-Based Global Locking

### Concept

Each backend (LLM, EMBED, RERANK) has its own lock. When a request hits a path, the proxy:
1. Identifies the backend for the path
2. Acquires locks for all backends configured in `config.yaml`
3. Executes the request
4. Releases locks

**Critical:** Backends do NOT lock themselves. If `llm` locks `embed` and `rerank`, it does NOT lock `llm`.

### Configuration

Edit `src/llmproxy/config.yaml`:

```yaml
global_lock:
  enabled: true
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
```

### Available Backends

- `llm`: `/v1/chat/completions`, `/v1/completions`, `/v1/models`, `/v1/models/{id}`
- `embed`: `/v1/embeddings`
- `rerank`: `/v1/rerank`, `/rerank`, `/v1/info`, `/info`

### Common Pitfalls

1. **Backends locking themselves**: Don't put `llm` in `llm.locks`
2. **Dynamic paths not matched**: Ensure `backend.py` has prefix matching for `/v1/models/{id}`
3. **Wrong tuple handling**: All endpoints must use `return_response=True`

## Common Issues

### 500 Internal Server Error on embeddings

**Cause**: `openai_embeddings()` endpoint unpacking bug

**Old code (wrong):**
```python
result, status = await app.state.embeddings.embeddings(request)  # Missing return_response=True
return JSONResponse(content=result, status_code=int(status))
```

**New code (correct):**
```python
result, status = await app.state.embeddings.embeddings(request, return_response=True)
return JSONResponse(content=result, status_code=int(status))
```

**Why this matters:** When `return_response=False` (default), the method returns only the body dict. When `return_response=True`, it returns `(body, status)` tuple. Unpacking a dict as a tuple causes `ValueError: too many values to unpack`.

### Dynamic paths not matched for locking

**Cause**: `/v1/models/xxx` paths not matched by `get_backend_for_path()`

**Solution**: Ensure `backend.py` has prefix matching:

```python
def get_backend_for_path(path: str) -> Backend | None:
    if path in PATH_TO_BACKEND:
        return PATH_TO_BACKEND[path]
    # Prefix matching for dynamic paths
    if path.startswith("/v1/models/") and path != "/v1/models":
        return Backend.LLM
    return None
```

### uv not found

**Cause**: `uv` not in PATH

**Solution**: Use full path:
```bash
~/.local/bin/uv run python -m src.llmproxy.main
# or
~/.cargo/bin/uv run python -m src.llmproxy.main
```

## Testing

Run integration tests:

```bash
cd /src/llmproxy
uv run pytest tests/test_global_locks.py -v
```

Expected: All 13 global lock tests pass

Full test suite:

```bash
cd /src/llmproxy
uv run pytest -v
```

Expected: 38/38 tests pass (backend servers must be running)

## Key Implementation Details

### Tuple handling

All component methods (`chat_completions`, `embeddings`, `rerank`, etc.) return `(body, status)` tuple when called with `return_response=True`. Ensure all endpoints use this consistently.

### Dynamic path matching

`get_backend_for_path()` uses prefix matching for dynamic paths like `/v1/models/{id}`. This ensures all model detail endpoints are matched to LLM backend for locking.

### Index preservation

TEI rerank results preserve original document indices: `index = item.get("index", i)`. This is critical for clients mapping results back to their input arrays.

### Hindsight compatibility

- `texts` → `documents`
- `return_text` → `return_documents`
- Auto-default `model="reranker"` if missing
- Auto-default `documents=["no documents provided"]` if empty

## Architecture

- `main.py`: FastAPI app, route definitions, global lock middleware
- `backend.py`: Backend enum (LLM, EMBED, RERANK), path mappings
- `components/openai.py`: OpenAI proxy, streaming detection
- `components/tei.py`: TEI rerank, Hindsight shims
- `components/embeddings.py`: Embeddings proxy

All use `httpx.AsyncClient` with tuned timeouts (30s connect / 90s read for LLM, 60s/120s for TEI).

## References

- [README.md](./README.md) - Full documentation
- [test.sh](./test.sh) - Integration tests
- [llmproxy.service](./llmproxy.service) - systemd unit file
- [src/llmproxy/config.yaml](./src/llmproxy/config.yaml) - Backend-based lock configuration
