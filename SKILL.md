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
- Routes requests to different llama-server backends (LLM + reranker)
- Provides OpenAI API compatibility (models, chat/completions, embeddings, streaming)
- Provides TEI rerank compatibility with Hindsight API shims
- Handles llama-server router-mode quirks (slow model loading, 404 on unloaded models)

## Quick Start

```bash
cd /src/llmproxy

# Set environment variables
export LLMPROXY_LLM_BASE_URL=http://127.0.0.1:8080
export LLMPROXY_RERANK_BASE_URL=http://127.0.0.1:8082
export LLMPROXY_PORT=4001

# Start the proxy
uv run python -m src.llmproxy.main
```

## Environment Variables

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `LLMPROXY_LLM_BASE_URL` | `http://127.0.0.1:8080` | Yes | LLM llama-server URL |
| `LLMPROXY_LLM_API_KEY` | `` | No | API key for LLM backend |
| `LLMPROXY_RERANK_BASE_URL` | `http://127.0.0.1:8082` | Yes | Reranker llama-server URL |
| `LLMPROXY_RERANK_API_KEY` | `` | No | API key for reranker backend |
| `LLMPROXY_HOST` | `0.0.0.0` | No | Listen address |
| `LLMPROXY_PORT` | `4001` | No | Listen port |
| `LLMPROXY_LOG_LEVEL` | `info` | No | Log level: `info`, `debug`, `trace` |

**Log levels:**
- **info**: Basic logs (endpoints, status, timing)
- **debug**: Full requests/responses with truncated content
- **trace**: Everything including full text (prompts, documents)

The systemd service defaults to `debug`.

## systemd Service

The project includes `llmproxy.service` for systemd deployment:

```ini
[Service]
Environment="LLMPROXY_LLM_BASE_URL=http://127.0.0.1:8080"
Environment="LLMPROXY_LLM_API_KEY="
Environment="LLMPROXY_RERANK_BASE_URL=http://127.0.0.1:8082"
Environment="LLMPROXY_RERANK_API_KEY="
Environment="LLMPROXY_HOST=0.0.0.0"
Environment="LLMPROXY_PORT=4001"
```

Deploy:
```bash
sudo cp llmproxy.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now llmproxy
sudo systemctl status llmproxy
```

## Testing

### Integration tests (smoke tests)

```bash
cd /src/llmproxy
bash test.sh
```

Tests cover:
- Health check
- TEI rerank (full TEI + Hindsight formats)
- OpenAI models list/detail
- Chat completions (sync + streaming)
- Completions (auto-model selection)
- Embeddings

**Expected behavior in router-mode:**
- Test accepts 500 (model loading) as valid for completions/embeddings
- Test accepts 404 for `/v1/models/{id}` (unloaded model)
- Streaming tests verify SSE format (`data:` prefix)

### Manual testing

**OpenAI chat (streaming):**
```bash
curl -X POST http://localhost:4001/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3.6-dense-mtp-custom","messages":[{"role":"user","content":"Hi"}],"stream":true}'
```

Expected: SSE stream with `data: {"id":"...","choices":[...]}...` and `data: [DONE]`

**TEI rerank:**
```bash
curl -X POST http://localhost:4001/v1/rerank \
  -H "Content-Type: application/json" \
  -d '{"query":"test","documents":["doc1","doc2","doc3"],"top_n":2}'
```

Expected: JSON array `[{index:0,score:0.92},{index:2,score:0.78}]` with original indices preserved

## Common Issues

### 500 Internal Server Error on completions/embeddings

**Cause**: Model not loaded in router-mode, takes 20-35s to load

**Solution**: Wait for model to load, or preload models by calling `/v1/chat/completions` first

### 404 on `/v1/models/{model_id}`

**Cause**: llama-server router mode returns 404 for unloaded models

**Solution**: This is expected behavior. Use `/v1/models` (list) instead, or load the model first

### Streaming returns 200 but no data

**Check**: Backend is actually streaming (call llama-server directly)
**Check**: Proxy logs show `streaming request for model=...`

### Rerank returns wrong indices

**Symptom**: `index` field is `0,1,2,...` instead of original document positions

**Cause**: Old code before index fix (v0.1.0+)

**Solution**: Ensure you have `original_index = item.get("index", i)` in `tei.py`

## Architecture

- `main.py`: FastAPI app, route definitions, JSONResponse with status codes
- `components/openai.py`: OpenAI proxy, streaming detection, `_forward_with_status()` helper
- `components/tei.py`: TEI rerank, Hindsight shims, index preservation

All use `httpx.AsyncClient` with:
- OpenAI: 30s connect / 90s read (120s for streaming)
- TEI: 60s connect / 120s read (for large batches)

## Key Implementation Details

### Streaming support

Detects `stream: true` in request body, returns `StreamingResponse(resp.aiter_lines(), media_type="text/event-stream")`

### Error forwarding

All endpoints return `(body, status)` tuple, `main.py` wraps with `JSONResponse(content=body, status_code=status)`

### Hindsight compatibility

- `texts` → `documents`
- `return_text` → `return_documents`
- Auto-default `model="reranker"` if missing
- Auto-default `documents=["no documents provided"]` if empty

### Index preservation

TEI rerank results: `index = item.get("index", i)` preserves backend's original document index, not sorted position

## References

- [README.md](./README.md) - Full documentation
- [test.sh](./test.sh) - Integration tests
- [llmproxy.service](./llmproxy.service) - systemd unit file
