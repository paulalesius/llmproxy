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

### Audio (STT / TTS) Compatibility
OpenAI-compatible audio endpoints (routed to dedicated backends):
- `POST /v1/audio/transcriptions` — Speech-to-text (multipart/form-data)
- `POST /v1/audio/translations` — Speech translation to English (multipart/form-data)
- `POST /v1/audio/speech` — Text-to-speech (JSON in → audio binary out)

### Production-Grade Capabilities
- **Global Locks** (optional): Serialize heavy endpoints (`/v1/chat/completions`, `/v1/completions`, `/v1/embeddings`) so they never run concurrently. Prevents backend overload. Can return `503` immediately or block and wait.
- **API Key Authentication** (optional): Protect all OpenAI endpoints with a simple Bearer token.
- **Request Hooks**: Run custom Python code before and after every request (after lock acquisition).
- **Configurable Logging**: Three levels (`info`, `debug`, `trace`) with intelligent truncation of large prompts/documents.
- **Router-Mode Optimized**: Generous timeouts and graceful handling of slow model loading (20–35 s on first request to a model in router mode).
- **Accurate Error Propagation**: All backend HTTP status codes (400, 429, 500, 504, etc.) are forwarded correctly to the client.
- **CLI Configuration**: Use `-c/--config` flag to specify config file path

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

The proxy routes requests to the appropriate specialized backend (LLM chat, embeddings, reranker, STT, or TTS) while adding cross-cutting features like global locking and authentication.

## Quick Start

```bash
cd llmproxy
uv sync

# Edit config.yaml or use your own config file
uv run python -m src.llmproxy.main -c /path/to/config.yaml
```

Configuration is done entirely through YAML config files. No environment variables are required.

## Configuration Reference

All configuration is done through YAML config files. See the example config below.

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

backends:
  # Default lock_script for all backends (can be overridden per-backend)
  lock_script: ""
  
  llm:
    base_url: http://127.0.0.1:8080
    locks:
      - embed
      - rerank
    # Can override default lock_script or set its own
    # lock_script: "/path/to/llm-specific-lock.sh"
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
- **`backends.lock_script: ""`** — optional default Python/shell/bash command for all backends (set at `backends:` level)
- **Per-backend `lock_script`** — override the default for a specific backend (set at `backends.llm.lock_script`, etc.)

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

The lock script runs for every backend request, regardless of whether global locks are enabled.

**Python scripts (.py):**
- Can define a `handle_request(request_data)` function
- `request_data` contains: `method`, `path`, `url`, `headers`, `phase`, `global_lock_enabled`
- `phase` is "pre" (before request) or "post" (after response)
- On post-phase: also includes `response_status`
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
  - `LOCK_SCRIPT_GLOBAL_LOCK_ENABLED` - "true" or "false" string

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

**Bash commands (raw command string in config.yaml):**
- If `lock_script` is not a file path, it's treated as a raw bash command
- Same environment variables as shell scripts
- Example in config.yaml:
```yaml
backends:
  lock_script: "echo 'Lock acquired' >> /var/log/llmproxy.lock"
```

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

### Audio – Transcriptions (STT)

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -F file="@audio.mp3" \
  -F model="whisper-large-v3"
```

### Audio – Speech (TTS)

```bash
curl -X POST http://localhost:8000/v1/audio/speech \
  -H "Content-Type: application/json" \
  -d '{
    "model": "tts-1",
    "input": "Hello world, this is a test.",
    "voice": "alloy"
  }' \
  --output speech.mp3
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

The service file uses `-c` flag to specify config path.

## Design Philosophy & Known Behaviors

- **Transparency first**: The proxy tries hard to be invisible. Status codes, streaming format, and error shapes from the backends are preserved.
- **Router mode is first-class**: First request to an unloaded model can take 20–35 seconds. Timeouts and error handling are tuned for this.
- **Index correctness in rerank** is non-negotiable — clients must be able to map results back to their original documents.
- **No magic**: If the backend returns 500 because a model is still loading, the proxy returns 500. This is the correct and expected behavior.
- The project was built with a pragmatic, "vibe-coded but functional" approach for real internal use (RAG pipelines, Hindsight, local OpenAI-compatible clients).

## Troubleshooting

### Global locks not working

1. Check that your `config.yaml` contains a `lock:` (or `global_lock:`) section with `enabled: true`.
2. Verify backend names are correct (`llm`, `embed`, `rerank`, `stt`, `tts`).
3. Make sure backends only list *other* backends they should lock (never themselves).
4. Pass your config with the `-c /path/to/config.yaml` flag.

### uv not found

`uv` is usually at `~/.local/bin/uv`. Add it to your PATH or use the full path.

### Audio endpoints return errors

Make sure you have `backends.stt` and `backends.tts` configured with valid URLs in your YAML (they default to localhost ports 8083/8084).

Apache License 2.0

---

**LLM Proxy** — One clean API in front of many specialized LLM backends (chat, embeddings, rerank, STT, TTS).
