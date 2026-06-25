"""
TEI (Text Embeddings Inference) compatible rerank endpoint.
Proxies to llama-server rerank endpoint.
"""

import os
import logging
import json
from typing import List, Optional, Any
from pydantic import BaseModel
import httpx
from . import config

logger = logging.getLogger(__name__)


def _log_request(level: str, endpoint: str, method: str, body: Optional[dict], headers: Optional[dict] = None):
    """Log request based on level. Avoids logging full text content unless trace."""
    log_func = getattr(logger, level, logger.debug)
    
    # Truncate text content for debug, include full for trace
    if body and level == "debug":
        body_copy = dict(body)
        # Truncate documents/texts
        for key in ["documents", "texts", "query"]:
            if key in body_copy:
                val = body_copy[key]
                if isinstance(val, list) and len(val) > 0:
                    if len(val[0]) > 100 if isinstance(val[0], str) else True:
                        body_copy[key] = [f"[{len(str(v))} chars]" for v in val[:3]]
                        if len(val) > 3:
                            body_copy[key].append(f"... and {len(val) - 3} more")
                elif isinstance(val, str) and len(val) > 200:
                    body_copy[key] = val[:200] + "..."
        log_body = body_copy
    else:
        log_body = body
    
    log_func(
        f"REQUEST [{method} {endpoint}]: "
        f"headers={headers}, "
        f"body={json.dumps(log_body, separators=(',', ':')) if log_body else None}"
    )


def _log_response(level: str, endpoint: str, status: int, body: Any, elapsed: float):
    """Log response based on level."""
    log_func = getattr(logger, level, logger.debug)
    
    # Truncate for debug
    if level == "debug" and isinstance(body, (dict, list)):
        if isinstance(body, list) and len(body) > 0:
            log_body = f"[{len(body)} results]"
        elif isinstance(body, dict):
            log_body = {k: f"[{len(str(v))} chars]" if isinstance(v, str) and len(v) > 100 else v 
                       for k, v in body.items()}
        else:
            log_body = body
    else:
        log_body = body
    
    log_func(
        f"RESPONSE [{endpoint}] {status} ({elapsed:.2f}s): "
        f"body={json.dumps(log_body, separators=(',', ':')) if log_body else None}"
    )


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
            timeout=httpx.Timeout(config.TEIRERANKER_TIMEOUT, read=config.TEIRERANKER_READ_TIMEOUT)
        )
        
        logger.info(f"TEIComponent initialized: base_url={self.base_url}, api_key={'*' * 8 if self.api_key else '(none)'}")
    
    async def rerank(self, request: RerankRequest) -> List[RerankResult]:
        """
        Proxy rerank request to llama-server.
        """
        import time
        start = time.time()
        
        log_level = os.environ.get("LLMPROXY_LOG_LEVEL", "info").lower()
        
        # Build payload (don't mutate the request object)
        documents = request.documents
        if request.texts is not None:
            # Hindsight uses 'texts' instead of 'documents'
            documents = request.texts
        
        # Default documents if not provided
        if documents is None or len(documents) == 0:
            documents = ["no documents provided"]

        # Spara den slutgiltiga dokumentlistan som skickas till backend
        # (används senare som fallback om backend inte returnerar document-text)
        sent_documents = documents

        # Default model if not provided
        model = request.model or "reranker"
        
        payload = {
            "model": model,
            "query": request.query,
            "documents": documents,
        }
        
        # Add optional parameters if provided
        if request.top_n is not None:
            payload["top_n"] = request.top_n
        if request.max_chunks_per_doc is not None:
            payload["max_chunks_per_doc"] = request.max_chunks_per_doc
        
        # Hindsight API compatibility: return_text instead of return_documents
        if request.return_text is not None:
            payload["return_documents"] = request.return_text
        elif request.return_documents is not None:
            payload["return_documents"] = request.return_documents
        
        # Build headers
        req_headers = {}
        if self.api_key:
            req_headers["Authorization"] = f"Bearer {self.api_key}"
        
        # Log request
        logger.info(f"LLMPROXY RERANK: model='{payload['model']}', "
                    f"query='{payload['query'][:80]}...', "
                    f"docs={len(payload['documents'])}, "
                    f"top_n={payload.get('top_n', 'N/A')}, "
                    f"return_docs={payload.get('return_documents', 'N/A')}")
        
        if log_level in ["debug", "trace"]:
            _log_request(log_level, "/rerank", "POST", payload, req_headers)
        
        # Forward to llama-server
        try:
            response = await self.client.post(
                "/rerank",
                json=payload,
                headers=req_headers
            )
            elapsed = time.time() - start
            
            # Log response
            if log_level in ["debug", "trace"]:
                try:
                    resp_body = response.json()
                except:
                    resp_body = response.text[:500]
                _log_response(log_level, "/rerank", response.status_code, resp_body, elapsed)
            
            response.raise_for_status()
            
            llama_response = response.json()
            
            # Handle both dict {"results": [...]} and raw list [...] responses
            if isinstance(llama_response, list):
                # Raw list response (some llama-server builds return this)
                results_raw = llama_response
            elif isinstance(llama_response, dict):
                # Dict response with "results" key
                results_raw = llama_response.get("results", [])
            else:
                logger.warning(f"Unexpected rerank response type: {type(llama_response)}")
                results_raw = []
            
            # Transform llama-server response to TEI format
            results = []
            for i, item in enumerate(results_raw):
                original_index = item.get("index", i)

                # Försök först få document-text från backend
                doc_text = item.get("text") or item.get("document")

                # Om return_documents=true och backend inte gav någon text → använd originalet
                if request.return_documents and doc_text is None:
                    if original_index < len(sent_documents):
                        doc_text = sent_documents[original_index]

                result = RerankResult(
                    index=original_index,
                    score=item.get("score") or item.get("relevance_score", 0.0),
                    document=doc_text
                )
                results.append(result)

            logger.info(f"Rerank complete: {len(results)} results in {elapsed:.2f}s")
            return results
            
        except httpx.HTTPStatusError as e:
            elapsed = time.time() - start
            logger.error(f"Rerank HTTP error {e.response.status_code} after {elapsed:.1f}s: {e.response.text[:200]}")
            raise
        except Exception as e:
            elapsed = time.time() - start
            logger.error(f"Rerank error after {elapsed:.1f}s: {e}")
            raise
    
    async def close(self):
        await self.client.aclose()
        logger.info("TEIComponent closed")
