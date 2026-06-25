"""
OpenAI-compatible embeddings endpoint.
Proxies to dedicated embeddings llama-server instance.
"""

import os
import logging
import json
from typing import Optional, Tuple, Any
import httpx
from .. import config

logger = logging.getLogger(__name__)


def _log_request(level: str, endpoint: str, method: str, body: Optional[dict], headers: Optional[dict] = None):
    """Log request based on level. Avoids logging full text content unless trace."""
    log_func = getattr(logger, level, logger.debug)
    
    if body and level == "debug":
        body_copy = dict(body)
        for key in ["input", "model"]:
            if key in body_copy and isinstance(body_copy[key], str) and len(body_copy[key]) > 200:
                body_copy[key] = body_copy[key][:200] + "..."
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
    
    if level == "debug" and isinstance(body, dict):
        body_copy = dict(body)
        for key in ["data", "model"]:
            if key in body_copy:
                body_copy[key] = f"[{len(str(body_copy[key]))} chars]"
        log_body = body_copy
    else:
        log_body = body
    
    log_func(
        f"RESPONSE [{endpoint}] {status} ({elapsed:.2f}s): "
        f"body={json.dumps(log_body, separators=(',', ':')) if log_body else None}"
    )


class EmbeddingsComponent:
    """Proxy component for OpenAI-compatible embeddings endpoint."""

    def __init__(self):
        self.base_url = os.environ.get(
            "LLMPROXY_EMBED_BASE_URL",
            "http://127.0.0.1:8081"
        )
        self.api_key = os.environ.get("LLMPROXY_EMBED_API_KEY", "")
        
        # Embeddings are typically fast, but allow for batch processing
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(config.OAIEMBEDDINGS_TIMEOUT, read=config.OAIEMBEDDINGS_READ_TIMEOUT)
        )
        
        logger.info(f"EmbeddingsComponent initialized: base_url={self.base_url}, api_key={'*' * 8 if self.api_key else '(none)'}")

    async def _forward_with_status(self, method: str, path: str, json: Optional[dict] = None) -> Tuple[Any, int]:
        """Forward a request and return (response_body, status_code) with logging."""
        import time
        start = time.time()
        
        req_headers = {}
        if self.api_key:
            req_headers["Authorization"] = f"Bearer {self.api_key}"
        
        log_level = os.environ.get("LLMPROXY_LOG_LEVEL", "info").lower()
        if log_level in ["debug", "trace"]:
            _log_request(log_level, path, method, json, req_headers)
        
        try:
            resp = await self.client.request(method, path, json=json, headers=req_headers)
            elapsed = time.time() - start
            
            if log_level in ["debug", "trace"]:
                try:
                    resp_body = resp.json()
                except:
                    resp_body = resp.text[:500]
                _log_response(log_level, path, resp.status_code, resp_body, elapsed)
            
            resp.raise_for_status()
            return resp.json(), resp.status_code
            
        except httpx.HTTPStatusError as e:
            elapsed = time.time() - start
            logger.warning(f"HTTP error {e.response.status_code} on {method} {path}: {e.response.text[:200]}")
            return e.response.json(), e.response.status_code
        except httpx.TimeoutException as e:
            elapsed = time.time() - start
            logger.warning(f"Timeout on {method} {path} after {elapsed:.1f}s: {e}")
            return {"error": {"message": "backend timeout", "type": "timeout"}}, 504
        except Exception as e:
            elapsed = time.time() - start
            logger.error(f"Error on {method} {path} after {elapsed:.1f}s: {e}")
            return {"error": {"message": str(e), "type": "server_error"}}, 500

    async def embeddings(self, body: dict, return_response: bool = False):
        """
        POST /v1/embeddings.
        
        Args:
            body: Request body with 'input' and optionally 'model'
            return_response: If True, return (body, status_code) tuple
        """
        log_level = os.environ.get("LLMPROXY_LOG_LEVEL", "info").lower()
        
        if log_level == "info":
            logger.info(f"embeddings request: model={body.get('model', 'default')}, "
                       f"input_type={type(body.get('input')).__name__}")
        
        body_result, status = await self._forward_with_status("POST", "/v1/embeddings", json=body)
        if return_response:
            return body_result, status
        return body_result

    async def close(self):
        await self.client.aclose()
        logger.info("EmbeddingsComponent closed")
