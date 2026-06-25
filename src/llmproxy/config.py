"""Configuration module for LLM Proxy."""

import os

# Environment variables
LOG_LEVEL = os.environ.get("LLMPROXY_LOG_LEVEL", "info").lower()
PORT = int(os.environ.get("LLMPROXY_PORT", "8000"))
API_KEY = os.environ.get("LLMPROXY_API_KEY", "")
OAILLM_BASE_URL = os.environ.get("LLMPROXY_OAILLM_BASE_URL", "")
TEIRERANKER_BASE_URL = os.environ.get("LLMPROXY_TEIRERANKER_BASE_URL", "")
LOCK_SCRIPT = os.environ.get("LLMPROXY_LOCK_SCRIPT", "")
LOCK_SCRIPT_PRE_CMD = os.environ.get("LLMPROXY_LOCK_SCRIPT_PRE_CMD", "pre")
LOCK_SCRIPT_POST_CMD = os.environ.get("LLMPROXY_LOCK_SCRIPT_POST_CMD", "post")

# Backend timeouts (in seconds)
# Connection timeout: time to establish connection
# Read timeout: time to wait for response data (longer for streaming)
OAILLM_TIMEOUT = int(os.environ.get("LLMPROXY_OAILLM_TIMEOUT", "30"))
OAILLM_READ_TIMEOUT = int(os.environ.get("LLMPROXY_OAILLM_READ_TIMEOUT", "90"))
TEIRERANKER_TIMEOUT = int(os.environ.get("LLMPROXY_TEIRERANKER_TIMEOUT", "60"))
TEIRERANKER_READ_TIMEOUT = int(os.environ.get("LLMPROXY_TEIRERANKER_READ_TIMEOUT", "120"))
OAIEMBEDDINGS_TIMEOUT = int(os.environ.get("LLMPROXY_OAIEMBEDDINGS_TIMEOUT", "30"))
OAIEMBEDDINGS_READ_TIMEOUT = int(os.environ.get("LLMPROXY_OAIEMBEDDINGS_READ_TIMEOUT", "60"))