"""Example hook script for BLProxy backends.

This demonstrates how to implement hooks for a backend.
Copy this file and modify for your needs.

Usage in config.yaml:
  backends:
    embed:
      url: http://localhost:8081
      paths:
        - /v1/embeddings
      script: /path/to/hook_example.py
"""

from blproxy.hooks import BackendHook, HookContext


class BackendHook:
    """Hook implementation for backend lifecycle events.
    
    All methods receive HookContext which contains:
    - backend_name: str - The backend identifier
    - request_method: str - HTTP method (GET, POST, etc.)
    - request_path: str - Full path being requested
    - request_headers: dict - Request headers
    - request_body: bytes | None - Request body (None for GET)
    - response_status: int | None - Response status code (None before response)
    - response_headers: dict | None - Response headers (None before response)
    - response_body: bytes | None - Response body (None before response)
    - error: str | None - Error message if request failed
    """
    
    def on_locks_acquired(self, context: HookContext) -> None:
        """Called after global locks are acquired, before request to backend.
        
        Use case: Log that locks were acquired, start timer, prepare resources.
        """
        print(f"[HOOK] Locks acquired for backend '{context.backend_name}'")
        print(f"  Request: {context.request_method} {context.request_path}")
    
    def on_before_request(self, context: HookContext) -> None:
        """Called right before request is sent to backend.
        
        Use case: Modify request headers, log request details, inject tracing.
        """
        print(f"[HOOK] About to send request to '{context.backend_name}'")
        if context.request_body:
            print(f"  Body size: {len(context.request_body)} bytes")
    
    def on_response(self, context: HookContext) -> None:
        """Called after response is received from backend.
        
        Use case: Log response status, parse response body, update metrics.
        """
        print(f"[HOOK] Received response from '{context.backend_name}'")
        print(f"  Status: {context.response_status}")
        if context.response_headers:
            content_type = context.response_headers.get('content-type', '')
            print(f"  Content-Type: {content_type}")
    
    def on_after_request(self, context: HookContext) -> None:
        """Called after request processing is complete, before locks are released.
        
        Use case: Clean up resources, log final state, update cache.
        """
        print(f"[HOOK] Request complete for '{context.backend_name}'")
        if context.error:
            print(f"  Error: {context.error}")
    
    def on_locks_released(self, context: HookContext) -> None:
        """Called after locks are released.
        
        Use case: Log lock release, notify other systems, update monitoring.
        """
        print(f"[HOOK] Locks released for backend '{context.backend_name}'")


# Async version example - uncomment to use async hooks
# class AsyncBackendHook:
#     async def on_locks_acquired(self, context: HookContext) -> None:
#         print(f"[ASYNC HOOK] Locks acquired for '{context.backend_name}'")
#         # Can await external APIs, databases, etc.
#
#     async def on_before_request(self, context: HookContext) -> None:
#         print(f"[ASYNC HOOK] Before request to '{context.backend_name}'")
#
#     async def on_response(self, context: HookContext) -> None:
#         print(f"[ASYNC HOOK] Response from '{context.backend_name}': {context.response_status}")
#
#     async def on_after_request(self, context: HookContext) -> None:
#         print(f"[ASYNC HOOK] After request for '{context.backend_name}'")
#
#     async def on_locks_released(self, context: HookContext) -> None:
#         print(f"[ASYNC HOOK] Locks released for '{context.backend_name}'")
