"""LLM Proxy - Multi-service proxy server."""

import os
from fastapi import FastAPI
from uvicorn import Config, Server
from .components.tei import TEIComponent

app = FastAPI(
    title="LLM Proxy",
    description="Proxy server for LLM services with TEI compatibility",
    version="0.1.0"
)

@app.on_event("startup")
async def startup():
    app.state.tei = TEIComponent()

@app.get("/")
async def root():
    return {"service": "llmproxy", "status": "running"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.post("/v1/rerank")
async def rerank(request: dict):
    """TEI-compatible rerank endpoint."""
    from pydantic import TypeAdapter
    from .components.tei import RerankRequest
    
    # Parse request
    adapter = TypeAdapter(RerankRequest)
    parsed = adapter.validate_python(request)
    
    # Call TEI component
    result = await app.state.tei.rerank(parsed)
    return result.model_dump()

def main():
    """Run the proxy server."""
    host = os.environ.get("LLMPROXY_HOST", "127.0.0.1")
    port = int(os.environ.get("LLMPROXY_PORT", "8000"))
    
    config = Config(app=app, host=host, port=port, log_level="info")
    server = Server(config=config)
    server.run()

if __name__ == "__main__":
    main()
