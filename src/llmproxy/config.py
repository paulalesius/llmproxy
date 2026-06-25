"""Configuration module for LLM Proxy."""

import os

# Environment variables
LOG_LEVEL = os.environ.get("LLMPROXY_LOG_LEVEL", "info").lower()
PORT = int(os.environ.get("LLMPROXY_PORT", "8000"))
API_KEY = os.environ.get("LLMPROXY_API_KEY", "")
OAILLM_BASE_URL = os.environ.get("LLMPROXY_OAILLM_BASE_URL", "")
TEIRERANKER_BASE_URL = os.environ.get("LLMPROXY_TEIRERANKER_BASE_URL", "")
LOCK_CONFIG = os.environ.get("LLMPROXY_LOCK_CONFIG", "")
REQUEST_PRE_PYSCRIPT = os.environ.get("LLMPROXY_REQUEST_PRE_PYSCRIPT", "")
REQUEST_POST_PYSCRIPT = os.environ.get("LLMPROXY_REQUEST_POST_PYSCRIPT", "")
