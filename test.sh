#!/bin/bash
set -e

# Install dependencies
uv sync

# Set environment variables
export LLMPROXY_TEI_BASE_URL="http://127.0.0.1:8082"
export LLMPROXY_HOST="127.0.0.1"
export LLMPROXY_PORT="4001"

# Start proxy in background
uv run python -m src.llmproxy.main &
PROXY_PID=$!
sleep 2

# Cleanup on exit
trap "kill $PROXY_PID 2>/dev/null || true" EXIT

# Test TEI rerank endpoint
curl -s -X POST "http://127.0.0.1:4001/v1/rerank" \
  -H "Content-Type: application/json" \
  -d '{"model":"rerank-model","query":"test query","documents":["document one","document two"],"top_n":2,"return_documents":true}'
