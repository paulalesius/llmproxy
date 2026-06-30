# EXRouter - Exclusive Router

A declarative backend proxy with global locking. Routes requests to configured backends and manages cross-backend resource locks.

![Banner](banner2.jpg)

## Purpose

EXRouter solves the common problem of having **separate backends** for different AI capabilities:

- One llama-server for chat & completions (router mode)
- One dedicated server for embeddings
- One server for reranking (TEI-compatible)
- Optional STT / TTS backends
- Any custom HTTP services that need resource coordination

Instead of clients talking to many different ports, EXRouter offers a single endpoint with transparent request forwarding and **global locking** to prevent resource contention.

## Key Features

- **Declarative Backend Configuration**: Define backends in YAML with paths and locks
- **Global Locking**: Backends can lock other backends while processing
- **Connection Pooling**: Shared httpx client for efficient connections
- **Streaming Support**: SSE and regular responses streamed without buffering
- **Timeout Handling**: Configurable lock timeouts with 503 + Retry-After
- **Hop-by-Hop Header Filtering**: Proper HTTP proxy behavior
- **Error Propagation**: Backend errors (4xx, 5xx) forwarded correctly

## Architecture

```
                     ┌─────────────────────────────┐
                     │         EXRouter            │
                     │   (FastAPI on :4001)        │
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
   /v1/chat/*             /v1/embeddings           /v1/rerank
   locks: [embed]         locks: [llm]             locks: [llm, embed]
```

## Quick Start

```bash
cd /src/exrouter
uv sync

# Edit config.yaml or use your own
uv run python -m src.exrouter.main -c /path/to/config.yaml
```

## Configuration

All configuration is done through YAML:

```yaml
server:
  host: 0.0.0.0
  port: 4001

# Global lock settings
global_lock:
  enabled: true
  timeout: 300  # seconds to wait for locks

backends:
  llm:
    url: http://127.0.0.1:8080
    paths:
      - /v1/chat/completions
      - /v1/completions
      - /v1/models
    locks:
      - embed
      - rerank

  embed:
    url: http://127.0.0.1:8081
    paths:
      - /v1/embeddings
    locks:
      - llm

  rerank:
    url: http://127.0.0.1:8082
    paths:
      - /v1/rerank
      - /rerank
    locks:
      - llm
      - embed
```

### Backend Configuration

Each backend specifies:

- `url`: Backend server URL
- `paths`: List of path patterns (supports wildcards like `/v1/vision/*`)
- `locks`: List of other backend names to lock while processing
- `script` (optional): Path to Python hook script for lifecycle callbacks

Example with hook script:

```yaml
backends:
  embed:
    url: http://127.0.0.1:8081
    paths:
      - /v1/embeddings
      - /embeddings
    locks: []
    script: /path/to/embed_hooks.py
```

### Hook Scripts

Hook scripts allow custom code to run at specific points in the request lifecycle.

Create a Python file that defines a `BackendHook` class inheriting from `exrouter.hooks.BackendHook`:

```python
from exrouter.hooks import BackendHook, HookContext

class BackendHook:
    def on_locks_acquired(self, context: HookContext) -> None:
        """Called after locks are acquired, before request to backend."""
        print(f"Locks acquired for {context.backend_name}")
    
    def on_before_request(self, context: HookContext) -> None:
        """Called right before request is sent to backend."""
        # Access context.request_method, context.request_path, context.request_headers, context.request_body
    
    def on_response(self, context: HookContext) -> None:
        """Called after response is received from backend."""
        # Access context.response_status, context.response_headers
    
    def on_after_request(self, context: HookContext) -> None:
        """Called after request processing, before locks are released."""
        # Access context.error if request failed
    
    def on_locks_released(self, context: HookContext) -> None:
        """Called after locks are released."""
        pass
```

Hook lifecycle order:

**Per-request hooks** (run on every request):
1. `on_locks_acquired()`
2. `on_before_request()`
3. Request forwarded to backend
4. `on_response()`
5. `on_after_request()`
6. `on_locks_released()`

**Backend lifecycle hooks** (recommended for service management):
- `on_backend_activated()` — first request after idle period
- `on_backend_deactivated()` — last request finished, backend now idle

**Backend lifecycle hooks (strongly recommended for service/resource management)**

These fire based on backend *activity level*, not per request. This is the cleanest way to start/stop systemd services when you have limited VRAM/GPU (e.g. `llm` and `stt_custom` cannot run at the same time).

- `on_backend_activated(context)` — Backend went from idle → active (first request after being quiet). Use this to start the required service and stop conflicting ones. Runs **only once** even if the backend then handles 50 concurrent or sequential requests to different paths.
- `on_backend_deactivated(context)` — Last in-flight request finished and backend is now idle. Use this to stop the service and free resources.

See the complete, ready-to-use example in `samples/hook.py` — it implements exactly the `llm` ↔ `stt_custom` switching pattern you need.

This approach is far cleaner and more efficient than putting `systemctl` calls inside `on_before_request`.

### Global Lock Settings

- `enabled`: Whether locking is active
- `timeout`: Seconds to wait for locks (returns 503 if exceeded)

## How Locking Works

EXRouter's locking is designed to prevent conflicting backends from running at the same time while still allowing natural concurrency inside the same backend.

### Key Rules

1. When a request arrives for a backend, EXRouter acquires the locks declared in that backend's `locks:` list.
2. **Re-entrant per backend**: Multiple concurrent requests to the *same* backend never block each other on locks, even if the backend declares locks on other backends. This is important for long-running requests (e.g. streaming chat completions) + follow-up requests (`/v1/models`, health checks, etc.).
3. A request to a *different* backend will wait if it tries to acquire a target that is currently held by another backend.
4. Locks are released only after the request finishes (including streaming).
5. If a backend declares no locks (`locks: []`), its requests never wait on the global lock system.

**Practical example with limited VRAM** (llm + stt_custom that cannot run together):

```yaml
backends:
  llm:
    url: http://127.0.0.1:8080
    paths: ["/v1/chat/completions", "/v1/models", ...]
    locks: [stt_custom]          # llm wants exclusive access to stt_custom's resources
    script: /path/to/hook.py

  stt_custom:
    url: http://127.0.0.1:8091
    paths: ["/transcribe"]
    locks: [llm]
    script: /path/to/hook.py
```

- Multiple requests to `llm` paths can run concurrently.
- A request to `stt_custom` while `llm` is active will wait (or trigger activation logic).
- The actual start/stop of systemd services is handled in the hook (see below).

Locks give you a declarative way to express "these two backends conflict".

## Response Handling

- **SSE (Server-Sent Events)**: Streamed line-by-line for chat completions
- **Regular responses**: Streamed byte-by-byte to avoid buffering
- **Timeouts**: Configurable (300s default, 30s connect)
- **Errors**: Backend HTTP status codes forwarded (400, 429, 500, 504, etc.)

## Testing

```bash
uv run pytest tests/ -v
```

All tests use mocked backends - no real servers required.

## Deployment (systemd)

Create a service file:

```ini
[Unit]
Description=EXRouter - Exclusive Router
After=network.target

[Service]
Type=simple
User=noname
WorkingDirectory=/src/exrouter
ExecStart=/src/exrouter/.venv/bin/python -m src.exrouter.main -c /src/exrouter/config.yaml
Restart=always

[Install]
WantedBy=multi-user.target
```

## Design Philosophy

- **Transparency**: EXRouter tries to be invisible - status codes and streaming preserved
- **Declarative**: All configuration in YAML, no code changes needed
- **Efficient**: Connection pooling and streaming to minimize resource usage
- **Robust**: Proper timeout handling and error propagation

## License

Apache License 2.0

---

**EXRouter** - One clean API in front of many specialized AI backends with global resource locking.
