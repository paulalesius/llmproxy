---
name: llmproxy
description: TEI-compatible rerank proxy — forwards requests from Hindsight to llama-server reranker, exposes /v1/info and /v1/rerank endpoints
category: mlops
---

# LLM Proxy (llmproxy)

## Syfte

LLM Proxy är en lättviktig proxy-server som översätter Hindsight API:s TEI-rerank-anrop till llama-server:s rerank-endpoint. Den fungerar som bro mellan Hindsight:s förväntade TEI-format och llama.cpp:s OpenAI-kompatibla API.

## Arkitektur

```
Hindsight API (port 9177)
    ↓ POST /v1/rerank (TEI-format)
LLM Proxy (port 4001)
    ↓ POST /rerank (llama-server-format)
llama-server reranker (port 8082)
```

## Endpoints

- `GET /v1/info` — TEI-info endpoint, returnerar modellinfo från llama-server
- `POST /v1/rerank` — TEI-kompatibel rerank, proxys till llama-server
- `GET /health` — Hälsokontroll

## Konfiguration

Miljövariabler:

```bash
LLMPROXY_TEI_BASE_URL=http://127.0.0.1:8082  # llama-server port
LLMPROXY_HOST=0.0.0.0
LLMPROXY_PORT=4001
```

## Starta

Via systemd:

```bash
systemctl start llmproxy.service
```

Manuellt:

```bash
cd /src/llmproxy
source .venv/bin/activate
export LLMPROXY_TEI_BASE_URL=http://127.0.0.1:8082
python -m src.llmproxy.main
```

## Vanliga fel

**404 på /v1/info**: Endpoint fanns inte före fix. Kolla att `/src/llmproxy/src/llmproxy/main.py` har `@app.get("/v1/info")` route.

**500 på /v1/rerank**: llama-server nere eller fel port. Kolla `llama-reranker.service` status och att port 8082 lyssnar.
