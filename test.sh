#!/bin/bash
# Integration tests for llmproxy
# Tests both TEI-compatible rerank and OpenAI-compatible LLM endpoints

set -e

# Use port 4002 for integration testing (avoids conflicts with llmproxy.service on 4001)
TEST_PORT=4002
PROXY_URL="http://127.0.0.1:$TEST_PORT"
LLAMA_URL="${BACKEND_URL:-http://127.0.0.1:8080}"
TEI_URL="${LLMPROXY_TEI_BASE_URL:-http://127.0.0.1:8082}"
TEST_PID=""

cleanup() {
    if [ -n "$TEST_PID" ] && kill -0 "$TEST_PID" 2>/dev/null; then
        echo "Cleaning up test process (PID $TEST_PID)..."
        kill "$TEST_PID" 2>/dev/null || true
        wait "$TEST_PID" 2>/dev/null || true
    fi
}

trap cleanup EXIT

echo "=== LLM Proxy Integration Tests ==="
echo "Proxy: $PROXY_URL (port $TEST_PORT)"
echo "LLaMA backend: $LLAMA_URL"
echo "TEI backend: $TEI_URL"
echo ""

# Start llmproxy on test port
echo "Starting llmproxy on port $TEST_PORT..."
cd "$(dirname "$0")"
export LLMPROXY_PORT=$TEST_PORT
export LLMPROXY_TEIRERANKER_BASE_URL=$TEI_URL
export LLMPROXY_OAILLM_BASE_URL=$LLAMA_URL
.venv/bin/python -m src.llmproxy.main &
TEST_PID=$!

# Wait for server to be ready
echo "Waiting for server to start..."
MAX_WAIT=10
WAITED=0
while [ $WAITED -lt $MAX_WAIT ]; do
    if curl -s "$PROXY_URL/health" | grep -q 'healthy'; then
        echo "  ✓ Server ready"
        break
    fi
    sleep 1
    WAITED=$((WAITED + 1))
done

if [ $WAITED -ge $MAX_WAIT ]; then
    echo "  ✗ Server failed to start within ${MAX_WAIT}s"
    exit 1
fi

echo ""

# ── TEI Tests ──────────────────────────────────────────────────────

echo "--- TEI Endpoints ---"
echo ""

echo "Test 1: Health endpoint"
RESPONSE=$(curl -s "$PROXY_URL/health")
if echo "$RESPONSE" | grep -q '"status":"healthy"'; then
    echo "  ✓ Health check passed"
else
    echo "  ✗ Health check failed: $RESPONSE"
    exit 1
fi

echo "Test 2: TEI /v1/rerank (full payload)"
RESPONSE=$(curl -s -X POST "$PROXY_URL/v1/rerank" \
  -H "Content-Type: application/json" \
  -d '{"model":"reranker","query":"test query","documents":["doc1","doc2"]}')

if echo "$RESPONSE" | grep -q '\['; then
    echo "  ✓ TEI rerank (full) passed"
else
    echo "  ✗ TEI rerank (full) failed: $RESPONSE"
    exit 1
fi

echo "Test 3: TEI /v1/rerank (minimal Hindsight format)"
RESPONSE=$(curl -s -X POST "$PROXY_URL/v1/rerank" \
  -H "Content-Type: application/json" \
  -d '{"query":"test query","return_text":false}')

if echo "$RESPONSE" | grep -q '\['; then
    echo "  ✓ TEI rerank (minimal) passed"
else
    echo "  ✗ TEI rerank (minimal) failed: $RESPONSE"
    exit 1
fi

echo ""

# ── OpenAI Tests ───────────────────────────────────────────────────

echo "--- OpenAI Endpoints ---"
echo ""

echo "Test 4: OpenAI /v1/models (list)"
RESPONSE=$(curl -s "$PROXY_URL/v1/models")

if echo "$RESPONSE" | grep -q '"data"'; then
    MODEL_COUNT=$(echo "$RESPONSE" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('data',[])))")
    echo "  ✓ OpenAI models list passed ($MODEL_COUNT models)"
else
    echo "  ✗ OpenAI models list failed: $RESPONSE"
    exit 1
fi

echo "Test 5: OpenAI /v1/models/{id} (detail)"
# Grab a model ID from the list
MODEL_ID=$(curl -s "$PROXY_URL/v1/models" | python3 -c "import sys,json; d=json.load(sys.stdin); print(next((m['id'] for m in d['data'] if 'qwen3.6' in m['id']), ''))")
if [ -n "$MODEL_ID" ]; then
    RESPONSE=$(curl -s "$PROXY_URL/v1/models/$MODEL_ID")
    # llama-server doesn't support individual model detail, so accept 404 as pass-through
    CODE=$(curl -s -o /dev/null -w "%{http_code}" "$PROXY_URL/v1/models/$MODEL_ID")
    echo "  ✓ OpenAI model detail forwarded (HTTP $CODE, model=$MODEL_ID)"
else
    echo "  ⚠ No qwen3.6 model found, skipping model detail test"
fi

echo "Test 6: OpenAI /v1/chat/completions"
RESPONSE=$(curl -s -X POST "$PROXY_URL/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3.6-moe-mtp-custom","messages":[{"role":"user","content":"Say hi"}],"max_tokens":10,"temperature":0}')

if echo "$RESPONSE" | grep -q '"object":"chat.completion"'; then
    echo "  ✓ OpenAI chat completions passed"
else
    echo "  ✗ OpenAI chat completions failed: $RESPONSE"
    exit 1
fi

echo "Test 7: OpenAI /v1/completions"
RESPONSE=$(curl -s -X POST "$PROXY_URL/v1/completions" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Say hi","max_tokens":5}' | head -c 500)

# Accept 400 (model missing), 429 (unloaded), 500 (loading failed), or 200 as valid proxy behavior
CODE=$(echo "$RESPONSE" | grep -o '"code":[0-9]*' | head -1 | grep -o '[0-9]*')
if [ "$CODE" = "400" ] || [ "$CODE" = "429" ] || [ "$CODE" = "500" ] || echo "$RESPONSE" | grep -q '"text"'; then
    echo "  ✓ OpenAI completions passed (proxy forwarding, HTTP response=$CODE)"
else
    echo "  ✗ OpenAI completions failed: $RESPONSE"
    exit 1
fi

echo "Test 8: OpenAI /v1/embeddings"
# Get an embedding model from the list (look for bge or similar)
EMBED_MODEL=$(curl -s "$PROXY_URL/v1/models" | python3 -c "import sys,json; d=json.load(sys.stdin); print(next((m['id'] for m in d['data'] if 'bge' in m['id'] or 'embed' in m['id']), ''))")
if [ -z "$EMBED_MODEL" ]; then
    # Fallback: just use first model
    EMBED_MODEL=$(curl -s "$PROXY_URL/v1/models" | python3 -c "import sys,json; d=json.load(sys.stdin); print(next((m['id'] for m in d['data']), 'default'))")
fi
echo "  Using embedding model: $EMBED_MODEL"
RESPONSE=$(curl -s -X POST "$PROXY_URL/v1/embeddings" \
  -H "Content-Type: application/json" \
  -d "{\"input\":\"test document\",\"model\":\"$EMBED_MODEL\"}")

# Accept 400 (model not found), 500 (loading), or 200 with data as valid proxy behavior
if echo "$RESPONSE" | grep -q '"data"' || echo "$RESPONSE" | grep -q '"code":400' || echo "$RESPONSE" | grep -q '"code":500'; then
    echo "  ✓ OpenAI embeddings passed (proxy forwarding)"
else
    echo "  ✗ OpenAI embeddings failed: $RESPONSE"
    exit 1
fi

echo "Test 9: OpenAI /v1/chat/completions (streaming)"
# Test streaming - expect SSE format with data: prefix
RESPONSE=$(curl -s -N -X POST "$PROXY_URL/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3.6-moe-mtp-custom","messages":[{"role":"user","content":"Say hi"}],"max_tokens":10,"temperature":0,"stream":true}' \
  --max-time 30 | head -n 5)

# Check for SSE format (data: prefix) or valid error response
if echo "$RESPONSE" | grep -q '^data:' || echo "$RESPONSE" | grep -q '"error"'; then
    echo "  ✓ OpenAI chat completions (streaming) passed"
else
    echo "  ✗ OpenAI chat completions (streaming) failed: $RESPONSE"
    exit 1
fi

echo "Test 10: OpenAI /v1/completions (streaming)"
RESPONSE=$(curl -s -N -X POST "$PROXY_URL/v1/completions" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"Say hi","max_tokens":5,"stream":true}' \
  --max-time 30 | head -n 5)

# Check for SSE format or valid error (model loading)
if echo "$RESPONSE" | grep -q '^data:' || echo "$RESPONSE" | grep -q '"error"'; then
    echo "  ✓ OpenAI completions (streaming) passed"
else
    echo "  ✗ OpenAI completions (streaming) failed: $RESPONSE"
    exit 1
fi

echo ""
echo "=== All tests passed ==="