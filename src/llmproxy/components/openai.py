"""
OpenAI-compatible proxy for llama-server.
Routes OpenAI API endpoints to llama-server's OpenAI-compatible API.
"""

import os
import logging
import json
from typing import Optional, Tuple, Any
from fastapi.responses import StreamingResponse, JSONResponse
import httpx
from .. import config

logger = logging.getLogger(__name__)


def _log_request(level: str, endpoint: str, method: str, body: Optional[dict], headers: Optional[dict] = None):
    """Log request based on level. Avoids logging full text content unless trace."""
    log_func = getattr(logger, level, logger.debug)
    
    # Truncate text content for debug, include full for trace
    if body and level == "debug":
        body_copy = dict(body)
        # Truncate long text fields
        for key in ["prompt", "messages", "input", "query"]:
            if key in body_copy and isinstance(body_copy[key], (str, list)):
                if isinstance(body_copy[key], str) and len(body_copy[key]) > 200:
                    body_copy[key] = body_copy[key][:200] + "..."
                elif isinstance(body_copy[key], list) and len(body_copy[key]) > 3:
                    body_copy[key] = body_copy[key][:3] + ["..."]
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
    if level == "debug" and isinstance(body, dict):
        body_copy = dict(body)
        for key in ["content", "text", "choices", "data"]:
            if key in body_copy:
                body_copy[key] = f"[{len(str(body_copy[key]))} chars]"
        log_body = body_copy
    else:
        log_body = body
    
    log_func(
        f"RESPONSE [{endpoint}] {status} ({elapsed:.2f}s): "
        f"body={json.dumps(log_body, separators=(',', ':')) if log_body else None}"
    )


class OpenAIComponent:
    """Proxy component for OpenAI-compatible endpoints."""

    def __init__(self):
        self.base_url = os.environ.get(
            "LLMPROXY_OAILLM_BASE_URL",
            "http://127.0.0.1:8080"
        )
        self.api_key = os.environ.get("LLMPROXY_OAILLM_API_KEY", "")
        
        # Router-mode: model loading can take 20s+, use config timeouts
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(config.OAILLM_TIMEOUT, read=config.OAILLM_READ_TIMEOUT)
        )
        
        logger.info(f"OpenAIComponent initialized: base_url={self.base_url}, api_key={'*' * 8 if self.api_key else '(none)'}")

    async def _forward_with_status(self, method: str, path: str, json: Optional[dict] = None, headers: Optional[dict] = None) -> Tuple[Any, int]:
        """
        Forward a request to llama-server and return (response_body, status_code).
        Logs request/response based on LLMPROXY_LOG_LEVEL.
        """
        import time
        start = time.time()
        
        # Build headers
        req_headers = headers or {}
        if self.api_key:
            req_headers["Authorization"] = f"Bearer {self.api_key}"
        
        # Log request
        log_level = os.environ.get("LLMPROXY_LOG_LEVEL", "info").lower()
        if log_level in ["debug", "trace"]:
            _log_request(log_level, path, method, json, req_headers)
        
        try:
            resp = await self.client.request(method, path, json=json, headers=req_headers)
            elapsed = time.time() - start
            
            # Log response
            if log_level in ["debug", "trace"]:
                try:
                    resp_body = resp.json()
                except:
                    resp_body = resp.text[:500]
                _log_response(log_level, path, resp.status_code, resp_body, elapsed)
            
            # Raise for error status codes
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

    async def models(self):
        """GET /v1/models - list available models."""
        body, status = await self._forward_with_status("GET", "/v1/models")
        return body, status

    async def model_detail(self, model_id: str):
        """GET /v1/models/{id} - get model details."""
        body, status = await self._forward_with_status("GET", f"/v1/models/{model_id}")
        return body, status

    async def chat_completions(self, body: dict, return_response: bool = False):
        """
        POST /v1/chat/completions.
        
        Args:
            body: Request body
            return_response: If True, return (StreamingResponse, status_code) for streaming,
                           or (body, status_code) for non-streaming.
        """
        is_stream = body.get("stream", False)
        log_level = os.environ.get("LLMPROXY_LOG_LEVEL", "info").lower()
        
        if is_stream:
            logger.info(f"chat_completions streaming request for model={body.get('model')}")
            if log_level == "trace":
                _log_request("trace", "/v1/chat/completions", "POST", body)
        else:
            if log_level in ["debug", "trace"]:
                _log_request(log_level, "/v1/chat/completions", "POST", body)
        
        if is_stream:
            try:
                resp = await self.client.post(
                    "/v1/chat/completions",
                    json=body,
                    headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
                    timeout=httpx.Timeout(config.OAILLM_TIMEOUT, read=config.OAILLM_READ_TIMEOUT + 210)
                )
                resp.raise_for_status()

                async def stream_generator():
                    async for chunk in resp.aiter_bytes():
                        yield chunk

                return StreamingResponse(
                    stream_generator(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    },
                    status_code=resp.status_code,
                ), resp.status_code

            except httpx.HTTPStatusError as e:
                logger.info(f"chat_completions streaming HTTP error: {e.response.status_code}")
                return e.response.json(), e.response.status_code
            except Exception as e:
                logger.error(f"chat_completions streaming error: {e}")
                return {"error": {"message": str(e), "type": "server_error"}}, 500
        else:
            body_result, status = await self._forward_with_status("POST", "/v1/chat/completions", json=body)
            if return_response:
                return body_result, status
            return body_result

    async def completions(self, body: dict, return_response: bool = False):
        """POST /v1/completions."""
        log_level = os.environ.get("LLMPROXY_LOG_LEVEL", "info").lower()
        
        if log_level in ["debug", "trace"]:
            logger.debug(f"completions request: model={body.get('model')}, stream={body.get('stream', False)}")

        if "model" not in body:
            body = dict(body)
            body["model"] = "default"
            logger.info("completions: no model specified, using model='default'")

        is_stream = body.get("stream", False)
        
        if is_stream:
            logger.info(f"completions streaming for model={body.get('model')}")
            if log_level == "trace":
                _log_request("trace", "/v1/completions", "POST", body)
            
            try:
                resp = await self.client.post(
                    "/v1/completions",
                    json=body,
                    headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
                    timeout=httpx.Timeout(config.OAILLM_TIMEOUT, read=config.OAILLM_READ_TIMEOUT + 30)
                )
                resp.raise_for_status()
                
                return StreamingResponse(
                    resp.aiter_lines(),
                    media_type="text/event-stream",
                    status_code=resp.status_code
                ), resp.status_code
                
            except httpx.HTTPStatusError as e:
                logger.info(f"completions streaming HTTP error: {e.response.status_code}")
                return e.response.json(), e.response.status_code
            except Exception as e:
                logger.error(f"completions streaming error: {e}")
                return {"error": {"message": str(e), "type": "server_error"}}, 500
        else:
            if log_level in ["debug", "trace"]:
                logger.debug(f"completions sending with model={body.get('model')}")
            
            body_result, status = await self._forward_with_status("POST", "/v1/completions", json=body)
            if return_response:
                return body_result, status
            return body_result


    async def close(self):
        await self.client.aclose()
        logger.info("OpenAIComponent closed")
