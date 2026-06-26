"""Backend routing configuration."""

from enum import Enum
from typing import Optional


class Backend(Enum):
    """Backend types with their path mappings."""
    
    LLM = "llm"
    EMBED = "embed"
    RERANK = "rerank"
    STT = "stt"
    TTS = "tts"
    
    @property
    def paths(self) -> list[str]:
        """Return paths that map to this backend."""
        mapping = {
            Backend.LLM: [
                "/v1/chat/completions",
                "/v1/completions",
                "/v1/models",
                "/v1/models/*",
                "/models",
                "/models/*",
            ],
            Backend.EMBED: ["/v1/embeddings"],
            Backend.RERANK: [
                "/v1/rerank",
                "/rerank",
                "/info",
                "/v1/info",
            ],
            Backend.STT: [
                "/v1/audio/transcriptions",
                "/v1/audio/translations",
            ],
            Backend.TTS: ["/v1/audio/speech"],
        }
        return mapping[self]
    
    @classmethod
    def for_path(cls, path: str) -> Optional["Backend"]:
        """Get backend for a given path."""
        for backend in cls:
            paths = backend.paths
            for p in paths:
                if p.endswith("*"):
                    prefix = p[:-1]
                    if path.startswith(prefix):
                        return backend
                elif path == p:
                    return backend
        return None


def get_backend_for_path(path: str) -> Optional[Backend]:
    """Get backend for a given path."""
    return Backend.for_path(path)
