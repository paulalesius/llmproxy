"""Test Backend path matching."""

import pytest
from src.exrouter.backend import Backend


def test_exact_path_match():
    """Test exact path matching."""
    backend = Backend(
        name="test",
        url="http://localhost:8080",
        paths=["/v1/chat/completions"],
        locks=[]
    )
    
    assert backend.matches_path("/v1/chat/completions") is True
    assert backend.matches_path("/v1/chat/completion") is False
    assert backend.matches_path("/v1/completions") is False


def test_wildcard_path_match():
    """Test wildcard path matching."""
    backend = Backend(
        name="vision",
        url="http://localhost:8081",
        paths=["/v1/vision/*"],
        locks=[]
    )
    
    assert backend.matches_path("/v1/vision/query") is True
    assert backend.matches_path("/v1/vision/images/test") is True
    assert backend.matches_path("/v1/vision") is False  # No trailing path
    assert backend.matches_path("/v1/chat/completions") is False


def test_multiple_paths():
    """Test backend with multiple paths."""
    backend = Backend(
        name="llm",
        url="http://localhost:8080",
        paths=["/v1/chat/completions", "/v1/completions", "/v1/models"],
        locks=[]
    )
    
    assert backend.matches_path("/v1/chat/completions") is True
    assert backend.matches_path("/v1/completions") is True
    assert backend.matches_path("/v1/models") is True
    assert backend.matches_path("/v1/embeddings") is False


def test_lock_targets():
    """Test getting lock targets."""
    backends = {
        "llm": Backend(name="llm", url="http://localhost:8080", paths=[], locks=["vision", "embed"]),
        "vision": Backend(name="vision", url="http://localhost:8081", paths=[], locks=[]),
        "embed": Backend(name="embed", url="http://localhost:8082", paths=[], locks=["llm"]),
    }
    
    llm = backends["llm"]
    assert llm.get_lock_targets(backends) == ["vision", "embed"]
    
    vision = backends["vision"]
    assert vision.get_lock_targets(backends) == []
    
    # Test with non-existent lock target
    backend_with_missing = Backend(
        name="test",
        url="http://localhost:8083",
        paths=[],
        locks=["nonexistent"]
    )
    assert backend_with_missing.get_lock_targets(backends) == []
