"""Test configuration loading."""

import pytest
from src.blproxy.config import Config, BackendConfig, ServerConfig, GlobalLockConfig


def test_config_from_dict():
    """Test loading config from dict."""
    data = {
        "server": {
            "host": "127.0.0.1",
            "port": 9000
        },
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
        "global_lock": {
            "enabled": True,
            "timeout": 60
        }
    }
    
    config = Config.from_dict(data)
    
    assert config.server.host == "127.0.0.1"
    assert config.server.port == 9000
    assert len(config.backends) == 2
    
    llm = config.backends["llm"]
    assert llm.url == "http://localhost:8080"
    assert llm.paths == ["/v1/chat/completions"]
    assert llm.locks == ["vision"]
    
    assert config.global_lock.enabled is True
    assert config.global_lock.timeout == 60


def test_config_defaults():
    """Test config with defaults."""
    data = {}
    
    config = Config.from_dict(data)
    
    assert config.server.host == "0.0.0.0"
    assert config.server.port == 4001
    assert len(config.backends) == 0
    assert config.global_lock.enabled is True
    assert config.global_lock.timeout == 300


def test_config_from_file(tmp_path):
    """Test loading config from file."""
    yaml_content = """
server:
  host: 0.0.0.0
  port: 4001

backends:
  llm:
    url: http://localhost:8080
    paths:
      - /v1/chat/completions
    locks:
      - vision

global_lock:
  enabled: true
  timeout: 120
"""
    
    config_file = tmp_path / "test_config.yaml"
    config_file.write_text(yaml_content)
    
    config = Config.from_file(str(config_file))
    
    assert config.server.port == 4001
    assert "llm" in config.backends
    assert config.backends["llm"].locks == ["vision"]
    assert config.global_lock.timeout == 120
