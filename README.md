# LLM Proxy

TEI-kompatibel proxy för rerank-operationer.

## Vad är detta?

Hindsight API förväntar sig en TEI (Text Embeddings Inference) server för reranking av sökresultat. llama.cpp:s `llama-server` har en inbyggd rerank-funktion men inte i TEI-format.

Denna proxy översätter:
- TEI `/v1/rerank` → llama-server `/rerank`
- TEI `/v1/info` → llama-server `/v1/models`

## Varför behövs den?

Utan denna proxy kraschar Hindsight API vid startup:

```
RuntimeError: Failed to connect to TEI server at http://127.0.0.1:4001/v1
```

## Hur den fungerar

1. Hindsight anropar `POST http://127.0.0.1:4001/v1/rerank`
2. llmproxy tar emot, transformerar till llama-server-format
3. llmproxy anropar `POST http://127.0.0.1:8082/rerank`
4. Response returneras till Hindsight

## Status

- **Aktiv**: Ja, körs på port 4001
- **Backend**: llama-server reranker på port 8082
- **Modell**: bge-reranker-v2-m3-Q4_0.gguf

## Felsökning

```bash
# Kolla att proxy är igång
curl http://127.0.0.1:4001/health

# Testa rerank direkt
curl -X POST http://127.0.0.1:4001/v1/rerank \
  -H "Content-Type: application/json" \
  -d '{"model":"bge-reranker-v2-m3","query":"test","documents":["doc1","doc2"]}'

# Kolla backend
curl http://127.0.0.1:8082/v1/models
```
