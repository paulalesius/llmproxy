# EXRouter - Exclusive Router

A declarative backend proxy with global locking and request remapping. Routes requests to configured backends and manages cross-backend resource locks.

![Banner](banner2.jpg)

## Purpose

EXRouter solves the common problem of having **separate backends** for different AI capabilities while providing a single, clean API in front of them.

It supports advanced routing needs through **request remapping**, allowing you to expose TEI-style endpoints on top of `llama-server --embeddings`, rewrite paths, normalize request formats between different APIs, and more — all declaratively.

## Key Features

- **Declarative Backend Configuration**: Define backends in YAML with paths and locks
- **Global Locking**: Backends can lock other backends while processing (with proper re-entrancy)
- **Request Remapping**: Per-backend Python scripts that can rewrite paths, fix request bodies, switch backends, or short-circuit responses
- **TEI Compatibility**: Easily expose TEI-style endpoints (`/v1/embed`, `/v1/info`) on top of `llama-server --embeddings`
- **Connection Pooling**: Shared `httpx` client for efficient connections
- **Streaming Support**: SSE and regular responses streamed without buffering
- **Timeout Handling**: Configurable lock timeouts with `503 + Retry-After`
- **Hop-by-Hop Header Filtering**: Proper HTTP proxy behavior
- **Lifecycle Hooks**: Run custom code on backend activation/deactivation (ideal for managing systemd services)
- **Error Propagation**: Backend HTTP status codes are forwarded correctly

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

global_lock:
  enabled: true
  timeout: 300

backends:
  llm:
    url: http://127.0.0.1:8080
    paths:
      - /v1/chat/completions
      - /v1/completions
      - /v1/models
    locks:
      - embed

  embed:
    url: http://127.0.0.1:8081
    paths:
      - /v1/embeddings
      - /v1/embed
      - /v1/info
    remapper: /path/to/tei_remapper.py
    locks: []
```

### Backend Configuration

Each backend specifies:

- `url`: Backend server URL
- `paths`: List of path patterns (supports wildcards like `/v1/vision/*`)
- `locks`: List of other backend names to lock while processing
- `script` (optional): Path to Python hook script for lifecycle callbacks
- `remapper` (optional): Path to Python request remapper script

### Request Remappers

Request remappers allow you to intercept and transform requests **before** they reach a backend. This is powerful for API compatibility.

Create a Python file that defines a `RequestRemapper` class:

```python
from exrouter.remapper import RequestRemapper, RemapResult
from exrouter.hooks import HookContext
import json

class RequestRemapper:
    async def remap(self, context: HookContext) -> RemapResult | None:
        path = context.request_path.lower()

        if path == "/v1/info":
            return RemapResult(
                status_code=200,
                content=json.dumps({"model_id": "my-model"}).encode(),
                response_headers={"content-type": "application/json"}
            )

        if path == "/v1/embed":
            # Rewrite path and fix body format
            data = json.loads(context.request_body or b"{}")
            if "inputs" in data:
                data["input"] = data.pop("inputs")

            return RemapResult(path="/v1/embeddings", body=json.dumps(data).encode())

        return None
```

Remappers can:
- Rewrite the request path
- Change the target backend
- Modify headers and body
- Return a direct response (short-circuit)

### Hook Scripts

Hook scripts allow custom code to run at specific points in the request lifecycle or backend lifecycle.

Create a Python file that defines a `BackendHook` class:

```python
from exrouter.hooks import BackendHook, HookContext

class BackendHook:
    def on_backend_activated(self, context: HookContext) -> None:
        print(f"Backend {context.backend_name} activated")

    def on_backend_deactivated(self, context: HookContext) -> None:
        print(f"Backend {context.backend_name} deactivated")

    # Other lifecycle methods available...
```

### Global Lock Settings

- `enabled`: Whether locking is active
- `timeout`: Seconds to wait for locks (returns 503 if exceeded)

## How Locking Works

EXRouter's locking prevents conflicting backends from running at the same time while still allowing natural concurrency inside the same backend.

**Key Rules:**
- Multiple concurrent requests to the *same* backend never block each other.
- A request to a *different* backend will wait if it tries to acquire a target currently held by another backend.
- Locks are released only after the request finishes (including streaming).

## Response Handling

- **SSE (Server-Sent Events)**: Streamed line-by-line
- **Regular responses**: Streamed byte-by-byte
- Backend HTTP status codes (including 4xx and 5xx) are forwarded correctly

## Testing

```bash
uv run pytest tests/ -v
```

## Deployment (systemd)

Example service file:

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

- **Transparency**: EXRouter tries to be invisible — status codes and streaming are preserved
- **Declarative**: All configuration lives in YAML
- **Extensible**: Remappers and hooks allow deep customization without changing core logic
- **Efficient**: Connection pooling and streaming minimize resource usage

## License

Apache License 2.0
