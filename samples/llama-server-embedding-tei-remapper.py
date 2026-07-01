"""TEI-compatible remapper for llama-server (fixed - uses httpx only)"""

import json
import httpx
from exrouter.remapper import RequestRemapper, RemapResult
from exrouter.hooks import HookContext

# Reuse httpx client (already available as dependency)
_client = httpx.AsyncClient(timeout=30.0)


class RequestRemapper:
    async def remap(self, context: HookContext) -> RemapResult | None:
        path = context.request_path.lower()

        # === /v1/info ===
        if path == "/v1/info":
            info = {
                "model_id": "bge-m3",
                "model_type": "text-embeddings",
                "max_input_length": 8192,
                "embedding_dim": 1024,
            }
            return RemapResult(
                status_code=200,
                content=json.dumps(info).encode(),
                response_headers={"content-type": "application/json"}
            )

        # === /v1/models ===
        if path in ("/v1/models", "/models"):
            return RemapResult(
                status_code=200,
                content=json.dumps({
                    "object": "list",
                    "data": [{"id": "bge-m3", "object": "model"}]
                }).encode(),
                response_headers={"content-type": "application/json"}
            )

        # === Handle embedding requests (TEI style) ===
        if path in ("/v1/embed", "/embed", "/v1/embeddings", "/embeddings"):
            if not context.request_body:
                return RemapResult(status_code=400, content=b"Empty body")

            try:
                data = json.loads(context.request_body)

                # Convert TEI-style "inputs" to OpenAI-style "input"
                if "inputs" in data and "input" not in data:
                    data["input"] = data.pop("inputs")

                print(f"[REMAPPER] Handling embedding request on {path}")

                # Call llama-server using httpx (already installed)
                resp = await _client.post(
                    "http://127.0.0.1:8081/v1/embeddings",
                    json=data
                )
                resp.raise_for_status()
                openai_resp = resp.json()

                # Return OpenAI-compatible format (not TEI list format)
                # Open WebUI expects {"data": [{"embedding": [...], "index": 0, "object": "embedding"}, ...]}
                return RemapResult(
                    status_code=200,
                    content=json.dumps(openai_resp).encode(),
                    response_headers={"content-type": "application/json"}
                )

            except Exception as e:
                print(f"[REMAPPER] Embedding error: {e}")
                return RemapResult(status_code=502, content=str(e).encode())

        return None
