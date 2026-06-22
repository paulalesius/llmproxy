"""
TEI (Text Embeddings Inference) compatible rerank endpoint.
Proxies to llama-server rerank endpoint.
"""

import os
from typing import List, Optional
from pydantic import BaseModel
import httpx


class RerankRequest(BaseModel):
    model: str
    query: str
    documents: List[str]
    top_n: Optional[int] = None
    max_chunks_per_doc: Optional[int] = None
    return_documents: Optional[bool] = None


class RerankResult(BaseModel):
    index: int
    relevance_score: float
    document: Optional[str] = None


class RerankResponse(BaseModel):
    model: str
    results: List[RerankResult]


class TEIComponent:
    """Proxy component for TEI rerank API."""
    
    def __init__(self):
        self.base_url = os.environ.get(
            "LLMPROXY_TEI_BASE_URL",
            "http://127.0.0.1:8082"
        )
        self.client = httpx.AsyncClient(base_url=self.base_url)
    
    async def rerank(self, request: RerankRequest) -> RerankResponse:
        """
        Proxy rerank request to llama-server.
        
        TEI format:
        {
          "model": "model-name",
          "query": "search query",
          "documents": ["doc1", "doc2"],
          "top_n": 5
        }
        
        llama-server format (router-mode):
        POST /rerank with similar structure
        """
        # Prepare request for llama-server
        payload = {
            "model": request.model,
            "query": request.query,
            "documents": request.documents,
        }
        
        # Add optional parameters if provided
        if request.top_n is not None:
            payload["top_n"] = request.top_n
        if request.max_chunks_per_doc is not None:
            payload["max_chunks_per_doc"] = request.max_chunks_per_doc
        if request.return_documents is not None:
            payload["return_documents"] = request.return_documents
        
        # Forward to llama-server
        response = await self.client.post(
            "/rerank",
            json=payload
        )
        response.raise_for_status()
        
        llama_response = response.json()
        
        # Transform llama-server response to TEI format
        results = []
        for i, item in enumerate(llama_response.get("results", [])):
            result = RerankResult(
                index=i,
                relevance_score=item.get("score", 0.0),
                document=item.get("text") or item.get("document") if request.return_documents else None
            )
            results.append(result)
        
        return RerankResponse(
            model=request.model,
            results=results
        )
    
    async def close(self):
        await self.client.aclose()
