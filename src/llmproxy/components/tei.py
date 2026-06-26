"""TEI-compatible rerank component. YAML config only."""

import logging
import time
from typing import List, Optional, Any
from pydantic import BaseModel
import httpx

from ..config import get_config

logger = logging.getLogger(__name__)


class RerankRequest(BaseModel):
    model: Optional[str] = None
    query: str
    documents: Optional[List[str]] = None
    texts: Optional[List[str]] = None          # Hindsight compat
    top_n: Optional[int] = None
    max_chunks_per_doc: Optional[int] = None
    return_documents: Optional[bool] = None
    return_text: Optional[bool] = None         # Hindsight compat


class RerankResult(BaseModel):
    index: int
    score: float
    document: Optional[str] = None


class RerankResponse(BaseModel):
    model: str
    results: List[RerankResult]


class TEIComponent:
    """Proxy component for TEI rerank API."""

    def __init__(self, client: httpx.AsyncClient | None = None):
        config = get_config()
        backend = config.backends.get("rerank")

        self.base_url = backend.url if backend else "http://127.0.0.1:8082"
        self.api_key = backend.api_key if backend else ""
        timeout = backend.timeout if backend else 30
        read_timeout = getattr(backend, "read_timeout", 120) if backend else 120

        if client is not None:
            self.client = client
        else:
            self.client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(timeout, read=read_timeout)
            )
        logger.info(f"TEIComponent ready → {self.base_url}")

    async def rerank(self, request: RerankRequest) -> tuple[dict, int]:
        start = time.time()

        # Hindsight compatibility
        documents = request.documents or request.texts or []
        if not documents:
            documents = ["no documents provided"]

        model = request.model or "reranker"
        return_documents = request.return_documents if request.return_documents is not None else (request.return_text or False)

        payload = {
            "model": model,
            "query": request.query,
            "documents": documents,
        }
        if request.top_n is not None:
            payload["top_n"] = request.top_n
        if request.max_chunks_per_doc is not None:
            payload["max_chunks_per_doc"] = request.max_chunks_per_doc
        if return_documents:
            payload["return_documents"] = True

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            resp = await self.client.post("/rerank", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Rerank backend error: {e}")
            # Handle non-JSON error responses gracefully
            try:
                error_data = e.response.json()
            except:
                error_data = {"error": str(e)}
            return error_data, e.response.status_code
        except Exception as e:
            logger.error(f"Rerank backend error: {e}")
            return {"error": {"message": str(e)}}, 502

        results = []
        for i, item in enumerate(data.get("results", [])):
            results.append(RerankResult(
                index=item.get("index", i),
                score=item.get("score", 0.0),
                document=item.get("document") or item.get("text") if return_documents else None
            ))

        logger.info(f"Rerank completed in {time.time()-start:.2f}s → {len(results)} results")
        # Return just the results list (TEI-compatible format)
        return {"results": [r.model_dump() for r in results]}, 200

    async def get_info(self) -> tuple[dict, int]:
        """Get backend info. Tries real TEI /info first, then falls back gracefully."""
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # Try the real TEI /info endpoint first
        try:
            resp = await self.client.get("/info", headers=headers)
            if resp.status_code == 200:
                return resp.json(), 200
        except Exception:
            pass

        # Fallback: llama-server style (uses /v1/models + /health)
        try:
            # Get model info
            models_resp = await self.client.get("/v1/models", headers=headers)
            models_resp.raise_for_status()
            models_data = models_resp.json()

            model_id = "reranker"
            if models_data.get("data"):
                model_id = models_data["data"][0].get("id", "reranker")

            # Check if backend is healthy
            health_resp = await self.client.get("/health", headers=headers)
            is_healthy = health_resp.status_code == 200

            return {
                "model_id": model_id,
                "revision": "llama-server",
                "max_concurrent_requests": 512,
                "healthy": is_healthy,
            }, 200

        except Exception as e:
            logger.error(f"Failed to get info from rerank backend: {e}")
            return {
                "model_id": "reranker",
                "revision": "unknown",
                "error": str(e)
            }, 200   # Return 200 so clients like Hindsight don't crash

    async def close(self):
        await self.client.aclose()
