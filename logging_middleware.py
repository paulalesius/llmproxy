import logging
import time

logger = logging.getLogger(__name__)

async def logging_middleware(request, call_next):
    start_time = time.time()
    
    logger.debug(f"REQUEST: {request.method} {request.url.path} {request.url.query if request.url.query else ''}")
    
    response = await call_next(request)
    
    duration = time.time() - start_time
    logger.debug(f"RESPONSE: {request.method} {request.url.path} -> {response.status_code} ({duration:.3f}s)")
    
    return response
