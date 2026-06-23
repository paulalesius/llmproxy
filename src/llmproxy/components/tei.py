"""
TEI (Text Embeddings Inference) compatible rerank endpoint.
Proxies to llama-server rerank endpoint.
"""

import os
from typing import List, Optional
from pydantic import BaseModel
import httpx


class RerankRequest(BaseModel):
    model: Optional[str] = None
    query: str
    documents: Optional[List[str]] = None
    # Hindsight API uses 'texts' instead of 'documents'
    texts: Optional[List[str]] = None
    top_n: Optional[int] = None
    max_chunks_per_doc: Optional[int] = None
    return_documents: Optional[bool] = None
    # Hindsight API compatibility: return_text instead of return_documents
    return_text: Optional[bool] = None


class RerankResult(BaseModel):
    index: int
    score: float  # TEI/Hindsight uses 'score', not 'relevance_score'
    document: Optional[str] = None


class RerankResponse(BaseModel):
    model: str
    results: List[RerankResult]


class TEIComponent:
    """Proxy component for TEI rerank API."""
    
    def __init__(self):
        self.base_url = os.environ.get(
            "LLMPROXY_TEIRERANKER_BASE_URL",
            "http://127.0.0.1:8082"
        )
        self.api_key = os.environ.get("LLMPROXY_TEIRERANKER_API_KEY", "")
        # Set timeout to 60s for large batches (Hindsight can send 1500+ docs)
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(60.0, read=120.0),
            headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        )
    
    async def rerank(self, request: RerankRequest) -> List[RerankResult]:
        """
        Proxy rerank request to llama-server.
        
        TEI format:
        {
          "model": "model-name",
          "query": "search query",
          "documents": ["doc1", "doc2"],
          "top_n": 5
        }
        
        Hindsight API format (simplified):
        {
          "query": "search query",
          "return_text": true/false
        }
        
        llama-server format (router-mode):
        POST /rerank with similar structure
        """
        import logging
        
        # Handle Hindsight API compatibility
        # Hindsight sends return_text instead of return_documents
        if request.return_text is not None:
            request.return_documents = request.return_text
        
        # Hindsight uses 'texts' instead of 'documents'
        if request.texts is not None:
            request.documents = request.texts
        
        # Default documents if not provided (llama-server requires non-empty array)
        if request.documents is None or len(request.documents) == 0:
            request.documents = ["no documents provided"]
        
        # Default model if not provided
        if request.model is None:
            request.model = "reranker"
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
        
        # LOG: Payload being sent to llama-server
        logging.info(f"LLMPROXY -> llama-server: POST /rerank, model='{payload['model']}', "
                     f"query='{payload['query'][:80]}...', docs={len(payload['documents'])}, "
                     f"top_n={payload.get('top_n', 'N/A')}, "
                     f"return_docs={payload.get('return_documents', 'N/A')}")
        
        # Forward to llama-server (router mode uses /rerank, not /v1/rerank)
        response = await self.client.post(
            "/rerank",
            json=payload
        )
        
        # LOG: Response status and body (truncated)
        response_body = response.text[:500] if response.text else "N/A"
        logging.info(f"LLMPROXY <- llama-server: {response.status_code}, body='{response_body}...'")
        
        response.raise_for_status()
        
        llama_response = response.json()
        
        # Transform llama-server response to TEI format
        results = []
        for i, item in enumerate(llama_response.get("results", [])):
            # Preserve original index from backend (TEI spec: maps back to input documents)
            # Fallback to enumerate position only if backend doesn't provide it
            original_index = item.get("index", i)
            result = RerankResult(
                index=original_index,
                score=item.get("relevance_score", 0.0),  # llama-server uses relevance_score, map to score
                document=item.get("text") or item.get("document") if request.return_documents else None
            )
            results.append(result)
        
        return results
    
    async def close(self):
        await self.client.aclose()
