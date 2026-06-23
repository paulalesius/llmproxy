"""
OpenAI-compatible proxy for llama-server.
Routes OpenAI API endpoints to llama-server's OpenAI-compatible API.
"""

import os
import logging
from typing import Optional, Tuple, Any
from fastapi.responses import StreamingResponse, JSONResponse
import httpx

logger = logging.getLogger(__name__)


class OpenAIComponent:
    """Proxy component for OpenAI-compatible endpoints."""

    def __init__(self):
        self.base_url = os.environ.get(
            "LLMPROXY_OAILLM_BASE_URL",
            "http://127.0.0.1:8080"
        )
        self.api_key = os.environ.get("LLMPROXY_OAILLM_API_KEY", "")
        # Router-mode: model loading can take 20s+, set longer timeouts
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(30.0, read=90.0),
            headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        )

    async def _forward_with_status(self, method: str, path: str, json: Optional[dict] = None) -> Tuple[Any, int]:
        """
        Forward a request to llama-server and return (response_body, status_code).
        Returns JSON body for non-streaming, or raw response for streaming.
        """
        try:
            resp = await self.client.request(method, path, json=json)
            # Raise for error status codes so we can handle them
            resp.raise_for_status()
            return resp.json(), int(resp.status_code)
        except httpx.HTTPStatusError as e:
            # Forward error responses from llama-server with proper status code
            logger.info(f"HTTP error {e.response.status_code}: {e.response.text[:200]}")
            return e.response.json(), int(e.response.status_code)
        except httpx.TimeoutException as e:
            logger.warning(f"Timeout on {method} {path}: {e}")
            return {"error": {"message": "backend timeout", "type": "timeout"}}, 504
        except Exception as e:
            logger.error(f"Error on {method} {path}: {e}")
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
                           If False, return body only (legacy mode).
        """
        is_stream = body.get("stream", False)
        
        if is_stream:
            # Streaming mode: return StreamingResponse
            logger.info(f"chat_completions streaming request for model={body.get('model')}")
            try:
                resp = await self.client.post(
                    "/v1/chat/completions",
                    json=body,
                    timeout=httpx.Timeout(30.0, read=120.0)
                )
                resp.raise_for_status()
                
                # Return StreamingResponse that proxies the SSE stream
                return StreamingResponse(
                    resp.aiter_lines(),
                    media_type="text/event-stream",
                    status_code=resp.status_code
                ), resp.status_code
                
            except httpx.HTTPStatusError as e:
                logger.info(f"chat_completions streaming HTTP error: {e.response.status_code}")
                return e.response.json(), e.response.status_code
            except Exception as e:
                logger.error(f"chat_completions streaming error: {e}")
                return {"error": {"message": str(e), "type": "server_error"}}, 500
        else:
            # Non-streaming mode: return JSON body
            body_result, status = await self._forward_with_status("POST", "/v1/chat/completions", json=body)
            if return_response:
                return body_result, status
            return body_result

    async def completions(self, body: dict, return_response: bool = False):
        """
        POST /v1/completions.
        
        Args:
            body: Request body
            return_response: If True, return (body, status_code) tuple.
        """
        logger.info(f"completions request: model={body.get('model')}, stream={body.get('stream', False)}")
        
        # llama-server requires a model name; fetch one if missing
        if "model" not in body:
            try:
                models_resp = await self.client.get("/v1/models")
                models = models_resp.json()
                if models.get("data"):
                    # Use first available model
                    default_model = models["data"][0]["id"]
                    body = dict(body)
                    body["model"] = default_model
                    logger.info(f"completions using default model: {default_model}")
                else:
                    logger.warning("completions: no models available")
            except Exception as e:
                logger.error(f"completions: failed to fetch models: {e}")
        
        is_stream = body.get("stream", False)
        
        if is_stream:
            # Streaming mode
            logger.info(f"completions streaming for model={body.get('model')}")
            try:
                resp = await self.client.post(
                    "/v1/completions",
                    json=body,
                    timeout=httpx.Timeout(30.0, read=120.0)
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
            # Non-streaming mode
            logger.info(f"completions sending with model={body.get('model')}")
            body_result, status = await self._forward_with_status("POST", "/v1/completions", json=body)
            if return_response:
                return body_result, status
            return body_result

    async def embeddings(self, body: dict, return_response: bool = False):
        """
        POST /v1/embeddings.
        
        Args:
            body: Request body
            return_response: If True, return (body, status_code) tuple.
        """
        body_result, status = await self._forward_with_status("POST", "/v1/embeddings", json=body)
        if return_response:
            return body_result, status
        return body_result

    async def close(self):
        await self.client.aclose()
