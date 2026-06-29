"""Test hook scripts are called at correct lifecycle points."""

import pytest
import tempfile
from pathlib import Path
from src.blproxy.config import Config
from src.blproxy.proxy import LockProxy
from src.blproxy.hooks import HookContext
from fastapi.testclient import TestClient


def create_test_hook_script(tmp_path: Path, hook_name: str) -> Path:
    """Create a test hook script that tracks call order."""
    
    script_content = f"""
from blproxy.hooks import BackendHook, HookContext

# Track which hooks were called
CALLS = []

class BackendHook:
    def on_locks_acquired(self, context: HookContext) -> None:
        CALLS.append("on_locks_acquired")
    
    def on_before_request(self, context: HookContext) -> None:
        CALLS.append("on_before_request")
    
    def on_response(self, context: HookContext) -> None:
        CALLS.append("on_response")
    
    def on_after_request(self, context: HookContext) -> None:
        CALLS.append("on_after_request")
    
    def on_locks_released(self, context: HookContext) -> None:
        CALLS.append("on_locks_released")

# Expose for testing
def get_calls():
    return CALLS.copy()

def reset_calls():
    CALLS.clear()
"""
    
    script_path = tmp_path / f"{hook_name}_hook.py"
    script_path.write_text(script_content)
    return script_path


@pytest.fixture
def hook_config(tmp_path):
    """Create config with hook script."""
    script_path = create_test_hook_script(tmp_path, "test")
    
    return Config.from_dict({
        "server": {"host": "127.0.0.1", "port": 9999},
        "backends": {
            "test_backend": {
                "url": "http://localhost:8080",
                "paths": ["/v1/test/*"],
                "locks": [],
                "script": str(script_path)
            }
        },
        "global_lock": {"enabled": False}
    })


def test_hooks_called_in_correct_order(hook_config):
    """Test that hooks are called in the correct lifecycle order."""
    proxy = LockProxy(hook_config)
    client = TestClient(proxy.app)
    
    # Make a request to trigger hooks
    response = client.get("/v1/test/endpoint")
    
    # Should reach backend (404 or 502 since backend doesn't exist)
    assert response.status_code in [404, 502]
    
    # Get the module from hook_loader
    test_module = proxy.hook_loader.get_module("test_backend")
    assert test_module is not None
    
    calls = test_module.get_calls()
    
    # Verify all 5 hooks were called in correct order
    expected_order = [
        "on_locks_acquired",
        "on_before_request", 
        "on_response",
        "on_after_request",
        "on_locks_released"
    ]
    
    assert calls == expected_order, f"Expected {expected_order}, got {calls}"


def test_hook_context_has_request_info(hook_config):
    """Test that HookContext contains correct request information."""
    
    # Create a new hook that captures context
    script_content = """
from blproxy.hooks import BackendHook, HookContext

CAPTURED_CONTEXT = None

class BackendHook:
    def on_locks_acquired(self, context: HookContext) -> None:
        pass
    
    def on_before_request(self, context: HookContext) -> None:
        global CAPTURED_CONTEXT
        CAPTURED_CONTEXT = {
            "backend_name": context.backend_name,
            "request_method": context.request_method,
            "request_path": context.request_path,
            "has_headers": context.request_headers is not None,
            "has_body": context.request_body is not None,
            "response_status": context.response_status,
            "error": context.error
        }
    
    def on_response(self, context: HookContext) -> None:
        if CAPTURED_CONTEXT:
            CAPTURED_CONTEXT["response_status_after"] = context.response_status
            CAPTURED_CONTEXT["has_response_headers"] = context.response_headers is not None
    
    def on_after_request(self, context: HookContext) -> None:
        pass
    
    def on_locks_released(self, context: HookContext) -> None:
        pass
"""
    
    script_path = Path(hook_config.backends["test_backend"].script)
    script_path.write_text(script_content)
    
    # Reload proxy with updated script
    proxy = LockProxy(hook_config)
    client = TestClient(proxy.app)
    
    # Make POST request with body
    response = client.post("/v1/test/endpoint", json={"key": "value"})
    assert response.status_code in [404, 502]
    
    # Get the module from hook_loader
    test_module = proxy.hook_loader.get_module("test_backend")
    assert test_module is not None
    
    captured = test_module.CAPTURED_CONTEXT
    
    assert captured["backend_name"] == "test_backend"
    assert captured["request_method"] == "POST"
    assert captured["request_path"] == "/v1/test/endpoint"
    assert captured["has_headers"] is True
    assert captured["has_body"] is True  # POST has body
    assert captured["response_status"] is None  # Before response
    assert captured["error"] is None
    
    # After response
    assert captured["response_status_after"] in [404, 502]
    assert captured["has_response_headers"] is True


def test_hook_error_context(hook_config):
    """Test that error is captured in HookContext when request fails."""
    
    script_content = """
from blproxy.hooks import BackendHook, HookContext

AFTER_REQUEST_CALLED = False
ERROR_CONTEXT = None

class BackendHook:
    def on_locks_acquired(self, context: HookContext) -> None:
        pass
    
    def on_before_request(self, context: HookContext) -> None:
        pass
    
    def on_response(self, context: HookContext) -> None:
        pass
    
    def on_after_request(self, context: HookContext) -> None:
        global AFTER_REQUEST_CALLED, ERROR_CONTEXT
        AFTER_REQUEST_CALLED = True
        ERROR_CONTEXT = context.error
    
    def on_locks_released(self, context: HookContext) -> None:
        pass
"""
    
    script_path = Path(hook_config.backends["test_backend"].script)
    script_path.write_text(script_content)
    
    proxy = LockProxy(hook_config)
    client = TestClient(proxy.app)
    
    # Make request - backend returns 404 (not an error, just backend response)
    response = client.get("/v1/test/endpoint")
    assert response.status_code == 404  # Backend 404, not proxy error
    
    # Get module from hook_loader
    test_module = proxy.hook_loader.get_module("test_backend")
    assert test_module is not None
    
    # on_after_request should be called
    assert test_module.AFTER_REQUEST_CALLED is True
    
    # error should be None since 404 is a normal response (not timeout/connection error)
    assert test_module.ERROR_CONTEXT is None


def test_async_hooks(hook_config):
    """Test that async hook methods work correctly."""
    
    script_content = """
import asyncio
from blproxy.hooks import BackendHook, HookContext

CALLS = []

class BackendHook:
    async def on_locks_acquired(self, context: HookContext) -> None:
        await asyncio.sleep(0.001)  # Simulate async work
        CALLS.append("async_on_locks_acquired")
    
    async def on_before_request(self, context: HookContext) -> None:
        await asyncio.sleep(0.001)
        CALLS.append("async_on_before_request")
    
    async def on_response(self, context: HookContext) -> None:
        await asyncio.sleep(0.001)
        CALLS.append("async_on_response")
    
    async def on_after_request(self, context: HookContext) -> None:
        await asyncio.sleep(0.001)
        CALLS.append("async_on_after_request")
    
    async def on_locks_released(self, context: HookContext) -> None:
        await asyncio.sleep(0.001)
        CALLS.append("async_on_locks_released")

def get_calls():
    return CALLS.copy()
"""
    
    script_path = Path(hook_config.backends["test_backend"].script)
    script_path.write_text(script_content)
    
    proxy = LockProxy(hook_config)
    client = TestClient(proxy.app)
    
    response = client.get("/v1/test/endpoint")
    assert response.status_code in [404, 502]
    
    # Get module from hook_loader
    test_module = proxy.hook_loader.get_module("test_backend")
    assert test_module is not None
    
    calls = test_module.get_calls()
    
    # All async hooks should be called
    expected = [
        "async_on_locks_acquired",
        "async_on_before_request",
        "async_on_response",
        "async_on_after_request",
        "async_on_locks_released"
    ]
    
    assert calls == expected


def test_backend_without_script(hook_config):
    """Test that backends without scripts work normally."""
    
    # Remove script from config
    hook_config.backends["test_backend"].script = None
    
    proxy = LockProxy(hook_config)
    client = TestClient(proxy.app)
    
    # Should work without errors
    response = client.get("/v1/test/endpoint")
    assert response.status_code in [404, 502]


def test_hooks_with_locks_enabled(tmp_path):
    """Test hooks work correctly with global locking enabled."""
    
    script_path = create_test_hook_script(tmp_path, "locked")
    
    config = Config.from_dict({
        "server": {"host": "127.0.0.1", "port": 9999},
        "backends": {
            "llm": {
                "url": "http://localhost:8080",
                "paths": ["/v1/chat/*"],
                "locks": ["embed"],
                "script": str(script_path)
            },
            "embed": {
                "url": "http://localhost:8081",
                "paths": ["/v1/embed/*"],
                "locks": []
            }
        },
        "global_lock": {"enabled": True, "timeout": 5}
    })
    
    proxy = LockProxy(config)
    client = TestClient(proxy.app)
    
    # Request to llm backend (which locks embed)
    response = client.post("/v1/chat/completions", json={"test": "data"})
    
    # Get module from hook_loader
    test_module = proxy.hook_loader.get_module("llm")
    assert test_module is not None
    
    calls = test_module.get_calls()
    
    # Should still call all hooks even with locking
    assert "on_locks_acquired" in calls
    assert "on_before_request" in calls
    assert "on_response" in calls
    assert "on_after_request" in calls
    assert "on_locks_released" in calls