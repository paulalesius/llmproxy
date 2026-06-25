#!/bin/bash
# Run llmproxy integration tests using uv
# This script sets up the environment and runs pytest

set -e

echo "=== LLM Proxy Integration Tests ==="
echo ""

# Get project root
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_ROOT"

# Use uv from ~/.local/bin/uv
UV_PATH="${UV_PATH:-/home/noname/.local/bin/uv}"

if [ ! -x "$UV_PATH" ]; then
    echo "Error: uv not found at $UV_PATH"
    exit 1
fi

echo "Using uv: $UV_PATH"
echo "Project: $PROJECT_ROOT"
echo ""

# Set test port (default 4002 to avoid conflicts with llmproxy.service on 4001)
export LLMPROXY_TEST_PORT="${LLMPROXY_TEST_PORT:-4002}"

# Backend URLs (use environment or defaults)
export BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8080}"
export LLMPROXY_TEI_BASE_URL="${LLMPROXY_TEI_BASE_URL:-http://127.0.0.1:8082}"
export LLMPROXY_EMBED_BASE_URL="${LLMPROXY_EMBED_BASE_URL:-http://127.0.0.1:8081}"

echo "Test configuration:"
echo "  Test port: $LLMPROXY_TEST_PORT"
echo "  LLaMA backend: $BACKEND_URL"
echo "  TEI backend: $LLMPROXY_TEI_BASE_URL"
echo "  Embeddings backend: $LLMPROXY_EMBED_BASE_URL"
echo ""

# Sync dependencies with uv
echo "Syncing dependencies with uv..."
"$UV_PATH" sync --extra test
echo "Dependencies synced"
echo ""

# Run pytest with verbose output
echo "Running integration tests..."
echo ""

# Run tests with pytest
"$UV_PATH" run pytest tests/ -v --timeout=60 $@

echo ""
echo "=== All tests completed ==="
