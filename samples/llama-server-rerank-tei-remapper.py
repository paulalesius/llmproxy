"""Remapper for llama-server --reranker"""

import json
from exrouter.remapper import RequestRemapper, RemapResult
from exrouter.hooks import HookContext


class RequestRemapper:
    def remap(self, context: HookContext) -> RemapResult | None:
        path = context.request_path.lower()

        # Normalize paths to what llama-server expects
        if path in ("/v1/rerank", "/v1/reranking", "/reranking"):
            return RemapResult(path="/rerank")

        # Optional: normalize body field names
        if path == "/rerank" and context.request_body:
            try:
                data = json.loads(context.request_body)

                # Some clients use "texts" instead of "documents"
                if "texts" in data and "documents" not in data:
                    data["documents"] = data.pop("texts")
                    return RemapResult(
                        body=json.dumps(data).encode("utf-8")
                    )
            except Exception:
                pass

        return None
