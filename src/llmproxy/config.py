"""Configuration module for LLM Proxy.

Supports both YAML config file and environment variable fallback.
"""

import os
from typing import Optional, Any
from dataclasses import dataclass, field
import yaml


@dataclass
class BackendConfig:
    """Configuration for a backend service."""
    base_url: str = ""
    api_key: str = ""
    timeout: int = 30
    read_timeout: int = 60
    locks: list = field(default_factory=list)


@dataclass
class ServerConfig:
    """Server configuration."""
    host: str = "0.0.0.0"
    port: int = 4001
    api_key: str = ""
    log_level: str = "info"


@dataclass
class GlobalLockConfig:
    """Global lock configuration (optional)."""
    enabled: bool = True  # Default to True if section exists
    locked_error: bool = False
    lock_script: str = ""
    
    @classmethod
    def from_yaml(cls, data: dict) -> "GlobalLockConfig":
        """Create from YAML data, with sensible defaults."""
        return cls(
            enabled=data.get("enabled", True),
            locked_error=data.get("locked_error", False),
            lock_script=data.get("lock_script", "")
        )


@dataclass
class Config:
    """Main configuration class."""
    server: ServerConfig = field(default_factory=ServerConfig)
    backends: dict = field(default_factory=dict)
    global_lock: Optional[GlobalLockConfig] = None  # Optional section
    
    def __post_init__(self):
        """Ensure backends dict has expected structure."""
        if not self.backends:
            self.backends = {
                "llm": BackendConfig(),
                "embed": BackendConfig(),
                "rerank": BackendConfig(),
            }


def load_config(config_path: Optional[str] = None) -> Config:
    """
    Load configuration from YAML file or environment variables.
    
    Args:
        config_path: Path to config.yaml file. If None, uses environment variables.
    
    Returns:
        Config object with all settings.
    """
    config = Config()
    
    if config_path and os.path.exists(config_path):
        # Load from YAML file
        _load_from_yaml(config_path, config)
    else:
        # Fall back to environment variables
        _load_from_env(config)
    
    return config


def _load_from_yaml(path: str, config: Config) -> None:
    """Load configuration from YAML file."""
    with open(path, "r") as f:
        data = yaml.safe_load(f) or {}
    
    # Server config
    if "server" in data:
        server_data = data["server"]
        config.server.host = server_data.get("host", "0.0.0.0")
        config.server.port = server_data.get("port", 4001)
        config.server.api_key = server_data.get("api_key", "")
        config.server.log_level = server_data.get("log_level", "info")
    
    # Global lock config (optional top-level section)
    if "global_lock" in data:
        config.global_lock = GlobalLockConfig.from_yaml(data["global_lock"])
    
    # Backends config
    if "backends" in data:
        backends_data = data["backends"]
        
        # Backend configurations
        for backend_name in ["llm", "embed", "rerank"]:
            if backend_name in backends_data:
                backend_config_data = backends_data[backend_name]
                backend_config = BackendConfig()
                
                backend_config.base_url = backend_config_data.get("base_url", "")
                backend_config.api_key = backend_config_data.get("api_key", "")
                backend_config.timeout = backend_config_data.get("timeout", 30)
                
                # Calculate read_timeout if not specified
                if "read_timeout" in backend_config_data:
                    backend_config.read_timeout = backend_config_data["read_timeout"]
                else:
                    # Default read timeouts based on backend type
                    if backend_name == "llm":
                        backend_config.read_timeout = 90
                    elif backend_name == "embed":
                        backend_config.read_timeout = 60
                    elif backend_name == "rerank":
                        backend_config.read_timeout = 120
                
                # Lock configuration
                backend_config.locks = backend_config_data.get("locks", [])
                
                config.backends[backend_name] = backend_config
            else:
                # Create empty config for missing backends
                config.backends[backend_name] = BackendConfig()


def _load_from_env(config: Config) -> None:
    """Load configuration from environment variables (fallback)."""
    # Server config
    config.server.host = os.environ.get("LLMPROXY_HOST", "0.0.0.0")
    config.server.port = int(os.environ.get("LLMPROXY_PORT", "4001"))
    config.server.api_key = os.environ.get("LLMPROXY_API_KEY", "")
    config.server.log_level = os.environ.get("LLMPROXY_LOG_LEVEL", "info").lower()
    
    # Global lock config (optional, only if env vars are set)
    lock_script = os.environ.get("LLMPROXY_LOCK_SCRIPT", "")
    if lock_script:
        config.global_lock = GlobalLockConfig(
            enabled=True,
            locked_error=os.environ.get("LLMPROXY_LOCKED_ERROR", "false").lower() == "true",
            lock_script=lock_script
        )
    
    # Backend configurations with defaults
    # LLM backend
    llm_config = BackendConfig()
    llm_config.base_url = os.environ.get("LLMPROXY_LLM_BASE_URL", "http://127.0.0.1:8080")
    llm_config.api_key = os.environ.get("LLMPROXY_LLM_API_KEY", "")
    llm_config.timeout = int(os.environ.get("LLMPROXY_LLM_TIMEOUT", "30"))
    llm_config.read_timeout = int(os.environ.get("LLMPROXY_LLM_READ_TIMEOUT", "90"))
    config.backends["llm"] = llm_config
    
    # Embeddings backend
    embed_config = BackendConfig()
    embed_config.base_url = os.environ.get("LLMPROXY_EMBED_BASE_URL", "http://127.0.0.1:8081")
    embed_config.api_key = os.environ.get("LLMPROXY_EMBED_API_KEY", "")
    embed_config.timeout = int(os.environ.get("LLMPROXY_EMBED_TIMEOUT", "30"))
    embed_config.read_timeout = int(os.environ.get("LLMPROXY_EMBED_READ_TIMEOUT", "60"))
    config.backends["embed"] = embed_config
    
    # Rerank backend
    rerank_config = BackendConfig()
    rerank_config.base_url = os.environ.get("LLMPROXY_RERANK_BASE_URL", "http://127.0.0.1:8082")
    rerank_config.api_key = os.environ.get("LLMPROXY_RERANK_API_KEY", "")
    rerank_config.timeout = int(os.environ.get("LLMPROXY_RERANK_TIMEOUT", "30"))
    rerank_config.read_timeout = int(os.environ.get("LLMPROXY_RERANK_READ_TIMEOUT", "120"))
    config.backends["rerank"] = rerank_config


# Global config instance (set by main.py)
CONFIG: Optional[Config] = None


def set_config(cfg: Config) -> None:
    """Set the global config instance."""
    global CONFIG
    CONFIG = cfg


def get_config() -> Config:
    """Get the global config instance."""
    if CONFIG is None:
        raise RuntimeError("Config not initialized. Call load_config() first.")
    return CONFIG
