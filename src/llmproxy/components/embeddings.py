"""Embeddings component. YAML config only."""

import logging
from typing import Any, Dict, Tuple
import httpx

from ..config import get_config

logger = logging.getLogger(__name__)


class EmbeddingsComponent:
    def __init__(self, client: httpx.AsyncClient | None = None):
        config = get_config()
        backend = config.backends.get("embed")

        self.base_url = backend.url if backend else "http://127.0.0.1:8081"
        self.api_key = backend.api_key if backend else ""
        timeout = backend.timeout if backend else 30
        read_timeout = getattr(backend, "read_timeout", 60) if backend else 60

        if client is not None:
            self.client = client
        else:
            self.client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(timeout, read=read_timeout)
            )
        logger.info(f"EmbeddingsComponent ready → {self.base_url}")

    async def embeddings(self, body: dict) -> Tuple[Dict[str, Any], int]:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            resp = await self.client.post("/v1/embeddings", json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data, resp.status_code
        except httpx.HTTPStatusError as e:
            return (e.response.json() if e.response.content else {"error": str(e)}), e.response.status_code
        except Exception as e:
            logger.error(f"Embeddings error: {e}")
            return {"error": {"message": str(e)}}, 502

    async def close(self):
        await self.client.aclose()
