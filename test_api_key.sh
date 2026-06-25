#!/bin/bash
# Test script for llmproxy API key authentication

set -e

echo "=== LLM Proxy API Key Authentication Test ==="

# Setup environment
export LLMPROXY_PORT=5001
export LLMPROXY_API_KEY="test-api-key-123"
export LLMPROXY_LLM_BASE_URL="http://127.0.0.1:8080"
export LLMPROXY_RERANK_BASE_URL="http://127.0.0.1:8082"
export LLMPROXY_EMBED_BASE_URL="http://127.0.0.1:8081"
export LLMPROXY_LOG_LEVEL="info"

BASE_URL="http://127.0.0.1:5001"

# Cleanup function
cleanup() {
    echo ""
    echo "Cleaning up..."
    pkill -f "python.*llmproxy.main" 2>/dev/null || true
}
trap cleanup EXIT

# Start the proxy server
echo "Starting llmproxy on port $LLMPROXY_PORT..."
cd "$(dirname "$0")"
python3 -m src.llmproxy.main &
SERVER_PID=$!

# Wait for server to be ready
echo "Waiting for server to start..."
MAX_WAIT=10
WAITED=0
while ! curl -s "$BASE_URL/health" > /dev/null 2>&1; do
    sleep 1
    WAITED=$((WAITED + 1))
    if [ $WAITED -ge $MAX_WAIT ]; then
        echo "ERROR: Server did not start within $MAX_WAIT seconds"
        exit 1
    fi
done
echo "Server is ready!"

# Test 1: Without API key (expect 401)
echo ""
echo "Test 1: Request without API key (expect 401)"
RESPONSE=$(curl -s -w "\n%{http_code}" "$BASE_URL/health")
STATUS=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | head -n-1)

if [ "$STATUS" = "401" ]; then
    echo "✓ PASS: Got 401 as expected"
    echo "  Response: $BODY"
else
    echo "✗ FAIL: Expected 401, got $STATUS"
    echo "  Response: $BODY"
    exit 1
fi

# Test 2: With wrong API key (expect 401)
echo ""
echo "Test 2: Request with wrong API key (expect 401)"
RESPONSE=$(curl -s -w "\n%{http_code}" -H "Authorization: Bearer wrong-key" "$BASE_URL/health")
STATUS=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | head -n-1)

if [ "$STATUS" = "401" ]; then
    echo "✓ PASS: Got 401 as expected"
    echo "  Response: $BODY"
else
    echo "✗ FAIL: Expected 401, got $STATUS"
    echo "  Response: $BODY"
    exit 1
fi

# Test 3: With correct API key as Bearer token (expect 200)
echo ""
echo "Test 3: Request with correct API key as Bearer token (expect 200)"
RESPONSE=$(curl -s -w "\n%{http_code}" -H "Authorization: Bearer test-api-key-123" "$BASE_URL/health")
STATUS=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | head -n-1)

if [ "$STATUS" = "200" ]; then
    echo "✓ PASS: Got 200 as expected"
    echo "  Response: $BODY"
else
    echo "✗ FAIL: Expected 200, got $STATUS"
    echo "  Response: $BODY"
    exit 1
fi

# Test 4: With correct API key as raw value (expect 200)
echo ""
echo "Test 4: Request with correct API key as raw value (expect 200)"
RESPONSE=$(curl -s -w "\n%{http_code}" -H "Authorization: test-api-key-123" "$BASE_URL/health")
STATUS=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | head -n-1)

if [ "$STATUS" = "200" ]; then
    echo "✓ PASS: Got 200 as expected"
    echo "  Response: $BODY"
else
    echo "✗ FAIL: Expected 200, got $STATUS"
    echo "  Response: $BODY"
    exit 1
fi

echo ""
echo "=== All tests passed! ==="
