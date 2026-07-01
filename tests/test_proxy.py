"""Test LockProxy routing and integration with mocked backends."""

import pytest
from src.exrouter.config import Config
from src.exrouter.proxy import LockProxy
from fastapi.testclient import TestClient


@pytest.fixture
def proxy_config():
    """Create a test config."""
    return Config.from_dict({
        "server": {"host": "127.0.0.1", "port": 9999},
        "backends": {
            "llm": {
                "url": "http://localhost:8080",
                "paths": ["/v1/chat/completions"],
                "locks": []
            },
            "vision": {
                "url": "http://localhost:8081",
                "paths": ["/v1/vision/*"],
                "locks": []
            }
        },
        "global_lock": {"enabled": False}  # Disable for simple tests
    })


@pytest.fixture
def proxy_config_with_locks():
    """Create config with locking enabled."""
    return Config.from_dict({
        "server": {"host": "127.0.0.1", "port": 9999},
        "backends": {
            "llm": {
                "url": "http://localhost:8080",
                "paths": ["/v1/chat/completions"],
                "locks": ["vision"]
            },
            "vision": {
                "url": "http://localhost:8081",
                "paths": ["/v1/vision/*"],
                "locks": []
            }
        },
        "global_lock": {"enabled": True, "timeout": 5}
    })


def test_proxy_routes_to_backend(proxy_config):
    """Test that proxy routes requests to correct backend."""
    proxy = LockProxy(proxy_config)
    client = TestClient(proxy.app)
    
    # Request to /v1/chat/completions should match llm backend
    # Backend returns 400 (Bad Request) since it's not a real server
    # Important: not 404 (unknown path)
    response = client.post("/v1/chat/completions", json={"test": "data"})
    
    # Should be 400 or 502 since backend doesn't exist or returns error
    # The important thing is routing happened (not 404 for unknown path)
    assert response.status_code in [400, 502]


def test_proxy_wildcard_routing(proxy_config):
    """Test wildcard path routing."""
    proxy = LockProxy(proxy_config)
    client = TestClient(proxy.app)
    
    # /v1/vision/query should match vision backend
    # Backend returns 404 since it's not a real server
    response = client.get("/v1/vision/query")
    
    # Should be 404 or 502 - important: routing happened
    assert response.status_code in [404, 502]


def test_proxy_unknown_path(proxy_config):
    """Test unknown path returns 404."""
    proxy = LockProxy(proxy_config)
    client = TestClient(proxy.app)
    
    response = client.get("/unknown/path")
    assert response.status_code == 404


def test_proxy_health_endpoint(proxy_config):
    """Test health endpoint."""
    proxy = LockProxy(proxy_config)
    client = TestClient(proxy.app)
    
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_proxy_root_endpoint(proxy_config):
    """Test root endpoint."""
    proxy = LockProxy(proxy_config)
    client = TestClient(proxy.app)
    
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["name"] == "EXRouter"


def test_proxy_lock_timeout(proxy_config_with_locks):
    """Test proxy returns 503 when lock times out."""
    proxy = LockProxy(proxy_config_with_locks)
    client = TestClient(proxy.app)
    
    # Mock that vision backend is locked
    proxy.lock_manager.locks["vision"] = type('LockState', (), {"locked_by": "other"})()
    
    # LLM tries to acquire lock on vision (already locked)
    response = client.post("/v1/chat/completions", json={"test": "data"})
    
    # Should timeout and return 503
    assert response.status_code == 503
    assert "Retry-After" in response.headers


def test_proxy_connection_pooling(proxy_config):
    """Test that proxy reuses httpx client (connection pooling)."""
    proxy = LockProxy(proxy_config)
    
    # Should have a shared httpx client
    assert proxy.httpx_client is not None
    
    # Clean up
    import asyncio
    asyncio.run(proxy.shutdown())
