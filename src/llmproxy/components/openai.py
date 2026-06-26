"""OpenAI-compatible component (chat, completions, models). YAML config only."""

import logging
import json
import time
from typing import Any, Dict, Optional, Tuple
import httpx
from fastapi.responses import StreamingResponse

from ..config import get_config

logger = logging.getLogger(__name__)


def _log_request(level: str, endpoint: str, method: str, body: Optional[dict]):
    if not body:
        return
    log_func = getattr(logger, level, logger.debug)
    log_func(f"REQUEST [{method} {endpoint}]: body={json.dumps(body, separators=(',',':'))[:500]}...")


class OpenAIComponent:
    def __init__(self, client: httpx.AsyncClient | None = None):
        config = get_config()
        backend = config.backends.get("llm")

        self.base_url = backend.url if backend else "http://127.0.0.1:8080"
        self.api_key = backend.api_key if backend else ""
        timeout = backend.timeout if backend else 30
        read_timeout = getattr(backend, "read_timeout", 90) if backend else 90

        if client is not None:
            self.client = client
        else:
            self.client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(timeout, read=read_timeout)
            )
        logger.info(f"OpenAIComponent ready → {self.base_url}")

    async def _request(self, method: str, path: str, json_body: dict = None, stream: bool = False):
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            if stream:
                req = self.client.build_request(method, path, json=json_body, headers=headers)
                resp = await self.client.send(req, stream=True)
                return resp  # Return response object directly for streaming
            else:
                resp = await self.client.request(method, path, json=json_body, headers=headers)
                resp.raise_for_status()
                return resp.json(), resp.status_code  # Return (json, status) tuple for non-streaming
        except httpx.HTTPStatusError as e:
            # Handle non-JSON error responses gracefully
            try:
                error_data = e.response.json()
            except:
                error_data = {"error": str(e)}
            return error_data, e.response.status_code
        except Exception as e:
            logger.error(f"OpenAI backend error on {path}: {e}")
            return {"error": {"message": str(e)}}, 502

    async def chat_completions(self, body: dict):
        is_stream = body.get("stream", False)
        result = await self._request("POST", "/v1/chat/completions", json_body=body, stream=is_stream)

        if is_stream:
            # result is an httpx.Response object when streaming
            status_code = result.status_code if hasattr(result, "status_code") else 200
            
            # If backend returned error status, return JSON error instead of streaming
            if status_code >= 400:
                try:
                    error_data = result.json()
                except:
                    error_data = {"error": {"message": f"Backend error: {status_code}"}}
                return error_data, status_code
            
            async def stream_gen():
                async for line in result.aiter_lines():
                    yield line + "\n"
            return StreamingResponse(stream_gen(), media_type="text/event-stream"), status_code

        # Always return tuple (data, status) for consistent error handling
        return result

    async def completions(self, body: dict):
        # Auto-fill model if missing
        if not body.get("model"):
            body = dict(body)
            body["model"] = "default"

        is_stream = body.get("stream", False)
        result = await self._request("POST", "/v1/completions", json_body=body, stream=is_stream)

        if is_stream:
            # result is an httpx.Response object when streaming
            status_code = result.status_code if hasattr(result, "status_code") else 200
            
            # If backend returned error status, return JSON error instead of streaming
            if status_code >= 400:
                try:
                    error_data = result.json()
                except:
                    error_data = {"error": {"message": f"Backend error: {status_code}"}}
                return error_data, status_code
            
            async def stream_gen():
                async for line in result.aiter_lines():
                    yield line + "\n"
            return StreamingResponse(stream_gen(), media_type="text/event-stream"), status_code

        # Always return tuple (data, status) for consistent error handling
        return result

    async def models(self):
        data, status = await self._request("GET", "/v1/models")
        return data, status

    async def model_detail(self, model_id: str):
        data, status = await self._request("GET", f"/v1/models/{model_id}")
        return data, status

    async def close(self):
        await self.client.aclose()
