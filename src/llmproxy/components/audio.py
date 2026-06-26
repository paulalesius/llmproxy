"""Audio components for STT (transcriptions/translations) and TTS (speech).
OpenAI-compatible. YAML config only (backends.stt and backends.tts)."""

import logging
from typing import Any, Dict, Tuple, Optional
import httpx
from fastapi import Request
from starlette.responses import Response

from ..config import get_config

logger = logging.getLogger(__name__)


class _BaseAudioComponent:
    """Base class for audio proxy components."""

    def __init__(self, backend_key: str, default_url: str, default_timeout: int = 120):
        config = get_config()
        backend = config.backends.get(backend_key)

        self.base_url = backend.url if backend else default_url
        self.api_key = backend.api_key if backend else ""
        timeout = backend.timeout if backend else default_timeout
        read_timeout = getattr(backend, "read_timeout", 300) if backend else 300  # Long for audio processing

        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, read=read_timeout)
        )
        logger.info(f"{self.__class__.__name__} ready → {self.base_url}")

    async def _forward(self, method: str, path: str, request: Request) -> httpx.Response:
        """Forward raw request (supports multipart/form-data for STT and JSON for TTS)."""
        headers = dict(request.headers)
        # Remove hop-by-hop headers that shouldn't be forwarded
        for h in ["host", "content-length", "connection", "keep-alive", "proxy-authenticate",
                  "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"]:
            headers.pop(h, None)

        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        content = await request.body()
        content_type = request.headers.get("content-type", "")

        if content_type:
            headers["content-type"] = content_type

        try:
            resp = await self.client.request(
                method,
                path,
                content=content,
                headers=headers,
            )
            return resp
        except httpx.HTTPStatusError as e:
            logger.error(f"Audio backend HTTP error on {path}: {e}")
            return e.response
        except Exception as e:
            logger.error(f"Audio backend error on {path}: {e}")
            # Return a fake error response
            return httpx.Response(
                status_code=502,
                json={"error": {"message": str(e), "type": "backend_error"}},
                headers={"content-type": "application/json"}
            )

    async def close(self):
        await self.client.aclose()


class STTComponent(_BaseAudioComponent):
    """STT component for OpenAI-compatible /v1/audio/transcriptions and /v1/audio/translations."""

    def __init__(self, client: httpx.AsyncClient | None = None):
        super().__init__(backend_key="stt", default_url="http://127.0.0.1:8083")

        if client is not None:
            self.client = client

    async def transcriptions(self, request: Request) -> httpx.Response:
        """Forward to backend /v1/audio/transcriptions (multipart/form-data)."""
        return await self._forward("POST", "/v1/audio/transcriptions", request)

    async def translations(self, request: Request) -> httpx.Response:
        """Forward to backend /v1/audio/translations (multipart/form-data)."""
        return await self._forward("POST", "/v1/audio/translations", request)


class TTSComponent(_BaseAudioComponent):
    """TTS component for OpenAI-compatible /v1/audio/speech."""

    def __init__(self, client: httpx.AsyncClient | None = None):
        super().__init__(backend_key="tts", default_url="http://127.0.0.1:8084", default_timeout=60)

        if client is not None:
            self.client = client

    async def speech(self, request: Request) -> httpx.Response:
        """Forward to backend /v1/audio/speech (JSON request, binary audio response)."""
        return await self._forward("POST", "/v1/audio/speech", request)
