"""
Backend definitions for llmproxy.

Each backend represents a group of related API endpoints that share
a common upstream service. Locking a backend locks all its paths.
"""

from enum import Enum
from typing import Set


class Backend(Enum):
    """Backend types that can be locked."""
    
    LLM = "llm"
    """LLM backend: /v1/chat/completions, /v1/completions, /v1/models, /v1/models/{id}"""
    
    EMBED = "embed"
    """Embeddings backend: /v1/embeddings"""
    
    RERANK = "rerank"
    """Reranker/TEI backend: /v1/rerank, /rerank, /v1/info, /info"""
    
    # Future backends (not yet implemented):
    # TTS = "tts"  # /v1/audio/speech
    # STT = "stt"  # /v1/audio/transcriptions, /v1/audio/translations


# Mapping from backend to all paths it exposes
BACKEND_PATHS: dict[Backend, Set[str]] = {
    Backend.LLM: {
        "/v1/chat/completions",
        "/v1/completions",
        "/v1/models",
        "/v1/models/{id}",
    },
    Backend.EMBED: {
        "/v1/embeddings",
    },
    Backend.RERANK: {
        "/v1/rerank",
        "/rerank",
        "/v1/info",
        "/info",
    },
}

# Reverse mapping: path -> backend
PATH_TO_BACKEND: dict[str, Backend] = {}
for backend, paths in BACKEND_PATHS.items():
    for path in paths:
        PATH_TO_BACKEND[path] = backend

# Backend name (string) -> Backend enum
BACKEND_NAME_TO_ENUM: dict[str, Backend] = {
    "llm": Backend.LLM,
    "embed": Backend.EMBED,
    "rerank": Backend.RERANK,
}


def get_backend_for_path(path: str) -> Backend | None:
    """Get the backend that owns a given path, or None if not found."""
    if path in PATH_TO_BACKEND:
        return PATH_TO_BACKEND[path]
    # Handle dynamic paths like /v1/models/xxx
    if path.startswith("/v1/models/") and path != "/v1/models":
        return Backend.LLM
    return None


def get_all_paths_for_backend(backend: Backend) -> Set[str]:
    """Get all paths owned by a backend."""
    return BACKEND_PATHS.get(backend, set())


def get_backends_for_paths(paths: Set[str]) -> Set[Backend]:
    """Get the set of backends that own any of the given paths."""
    backends = set()
    for path in paths:
        backend = get_backend_for_path(path)
        if backend:
            backends.add(backend)
    return backends
