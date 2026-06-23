"""
OpenAI-compatible embeddings proxy for dedicated embedding server.
Separate from LLM server for better resource management.
"""

import os
import logging
import json
from typing import Optional, Tuple, Any
import httpx

logger = logging.getLogger(__name__)


def _log_request(level: str, endpoint: str, method: str, body: Optional[dict], headers: Optional[dict] = None):
    """Log request based on level. Avoids logging full text content unless trace."""
    log_func = getattr(logger, level, logger.debug)
    
    if body and level == "debug":
        body_copy = dict(body)
        for key in ["input", "prompt"]:
            if key in body_copy:
                val = body_copy[key]
                if isinstance(val, str) and len(val) > 200:
                    body_copy[key] = val[:200] + "..."
                elif isinstance(val, list) and len(val) > 3:
                    body_copy[key] = val[:3] + ["..."]
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
        for key in ["embedding", "data"]:
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
    """Proxy component for dedicated embeddings server."""

    def __init__(self):
        self.base_url = os.environ.get(
            "LLMPROXY_OAIEMBEDDINGS_BASE_URL",
            "http://127.0.0.1:8081"
        )
        self.api_key = os.environ.get("LLMPROXY_OAIEMBEDDINGS_API_KEY", "")
        
        # Embeddings are fast, but allow for batch requests
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(30.0, read=60.0)
        )
        
        logger.info(f"EmbeddingsComponent initialized: base_url={self.base_url}, api_key={'*' * 8 if self.api_key else '(none)'}")

    async def embeddings(self, body: dict, return_response: bool = False):
        """
        POST /v1/embeddings - proxy to dedicated embeddings server.
        
        Args:
            body: Request body with 'input' and 'model'
            return_response: If True, return (body, status_code) tuple
        """
        import time
        start = time.time()
        
        log_level = os.environ.get("LLMPROXY_LOG_LEVEL", "info").lower()
        
        # Log request
        if log_level in ["debug", "trace"]:
            _log_request(log_level, "/v1/embeddings", "POST", body)
        else:
            logger.info(f"embeddings request: model={body.get('model')}, "
                        f"input_type={type(body.get('input')).__name__}, "
                        f"input_count={len(body.get('input', [])) if isinstance(body.get('input'), list) else 1}")
        
        # Build headers
        req_headers = {}
        if self.api_key:
            req_headers["Authorization"] = f"Bearer {self.api_key}"
        
        try:
            resp = await self.client.post(
                "/v1/embeddings",
                json=body,
                headers=req_headers
            )
            elapsed = time.time() - start
            
            # Log response
            if log_level in ["debug", "trace"]:
                try:
                    resp_body = resp.json()
                except:
                    resp_body = resp.text[:500]
                _log_response(log_level, "/v1/embeddings", resp.status_code, resp_body, elapsed)
            else:
                logger.info(f"embeddings response: status={resp.status_code}, "
                            f"elapsed={elapsed:.2f}s")
            
            resp.raise_for_status()
            result = resp.json()
            
            if return_response:
                return result, resp.status_code
            return result
            
        except httpx.HTTPStatusError as e:
            elapsed = time.time() - start
            logger.warning(f"embeddings HTTP error {e.response.status_code} after {elapsed:.1f}s: {e.response.text[:200]}")
            result = e.response.json()
            if return_response:
                return result, e.response.status_code
            return result
        except httpx.TimeoutException as e:
            elapsed = time.time() - start
            logger.warning(f"embeddings timeout after {elapsed:.1f}s: {e}")
            result = {"error": {"message": "backend timeout", "type": "timeout"}}
            if return_response:
                return result, 504
            return result
        except Exception as e:
            elapsed = time.time() - start
            logger.error(f"embeddings error after {elapsed:.1f}s: {e}")
            result = {"error": {"message": str(e), "type": "server_error"}}
            if return_response:
                return result, 500
            return result

    async def close(self):
        await self.client.aclose()
        logger.info("EmbeddingsComponent closed")
