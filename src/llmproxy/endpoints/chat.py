"""Chat endpoints - delegates to modern OpenAIComponent."""

import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..config import get_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["chat"])


@router.post("/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    app = request.app

    # Use the modern component
    result = await app.state.openai.chat_completions(body)

    # Handle streaming vs normal response
    if isinstance(result, tuple):
        # Non-streaming: (data, status)
        data, status = result
        return JSONResponse(content=data, status_code=status)
    else:
        # Streaming case - result is already a StreamingResponse
        return result


@router.post("/completions")
async def completions(request: Request):
    body = await request.json()
    app = request.app

    result = await app.state.openai.completions(body)

    if isinstance(result, tuple):
        data, status = result
        return JSONResponse(content=data, status_code=status)
    else:
        return result
