"""Test Pydantic validation of config."""

import pytest
from pydantic import ValidationError
from blproxy.config import Config


def test_config_validates_correctly():
    """Test that valid config passes validation."""
    config_dict = {
        "server": {
            "host": "127.0.0.1",
            "port": 8080
        },
        "backends": {
            "llm": {
                "url": "http://127.0.0.1:8080",
                "paths": ["/v1/chat/*"],
                "locks": ["vision"]
            },
            "vision": {
                "url": "http://127.0.0.1:8081",
                "paths": ["/v1/vision/*"]
            }
        },
        "global_lock": {
            "enabled": True,
            "timeout": 300
        }
    }
    
    config = Config.from_dict(config_dict)
    
    assert config.server.host == "127.0.0.1"
    assert config.server.port == 8080
    assert "llm" in config.backends
    assert "vision" in config.backends
    assert config.backends["llm"].locks == ["vision"]
    assert config.backends["vision"].locks == []
    assert config.global_lock.enabled is True
    assert config.global_lock.timeout == 300


def test_config_missing_required_backend_url():
    """Test that missing url field raises ValidationError."""
    config_dict = {
        "backends": {
            "llm": {
                "paths": ["/v1/chat/*"]  # Missing url
            }
        }
    }
    
    with pytest.raises(ValidationError) as exc_info:
        Config.from_dict(config_dict)
    
    # Check that error mentions 'url'
    assert "url" in str(exc_info.value).lower()


def test_config_missing_paths():
    """Test that missing paths uses default empty list."""
    config_dict = {
        "backends": {
            "llm": {
                "url": "http://127.0.0.1:8080"
                # paths is optional
            }
        }
    }
    
    config = Config.from_dict(config_dict)
    assert config.backends["llm"].paths == []


def test_config_default_values():
    """Test that defaults are applied correctly."""
    config_dict = {}  # Empty config
    
    config = Config.from_dict(config_dict)
    
    assert config.server.host == "0.0.0.0"
    assert config.server.port == 4001
    assert config.backends == {}
    assert config.global_lock.enabled is True
    assert config.global_lock.timeout == 300


def test_config_invalid_port():
    """Test that invalid port type raises ValidationError."""
    config_dict = {
        "server": {
            "host": "127.0.0.1",
            "port": "not-a-number"  # Should be int
        }
    }
    
    with pytest.raises(ValidationError) as exc_info:
        Config.from_dict(config_dict)
    
    assert "port" in str(exc_info.value).lower()
