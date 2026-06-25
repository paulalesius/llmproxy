"""Configuration module for LLM Proxy.

Primary mechanism: YAML file via `python -m src.llmproxy.main -c /path/to/config.yaml`

The YAML should contain:
  server: {host, port, api_key, log_level}
  backends:
    llm: {base_url, api_key, timeout, read_timeout, locks: [...], lock_script: ""}
    embed: ...
    rerank: ...
  global_lock: {enabled: true, locked_error: false, lock_script: ""}   # presence + enabled controls locking

Environment variables (LLMPROXY_*) are only used as fallback when NO config file is provided.
This allows gradual migration; new deployments should use config.yaml exclusively.
"""

import os
import logging
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
    lock_script: str = ""  # Per-backend lock script (optional)


@dataclass
class ServerConfig:
    """Server configuration."""
    host: str = "0.0.0.0"
    port: int = 4001
    api_key: str = ""
    log_level: str = "info"


@dataclass
class GlobalLockConfig:
    """Global lock configuration (optional, controls enabled/locked_error).

    lock_script is supported for backwards compatibility (global default hook).
    Prefer per-backend lock_script or backends.lock_script in config for new setups.
    """
    enabled: bool = True  # Default to True if section exists
    locked_error: bool = False
    lock_script: str = ""  # Optional global default lock script hook

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
    global_lock: GlobalLockConfig = field(default_factory=lambda: GlobalLockConfig(enabled=False))
    backends_raw: dict = field(default_factory=dict)  # Raw backends section for defaults (legacy for lock_script default)

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

        # Extract default lock_script at backends level (applies to all backends)
        backends_default_lock_script = backends_data.get("lock_script", "")
        if backends_default_lock_script:
            config.backends_default_lock_script = backends_default_lock_script
            logger.debug(f"Default backends.lock_script configured: {backends_default_lock_script}")

        # Store raw backends data for fallback (legacy support for global_lock.lock_script)
        config.backends_raw = backends_data

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

                # Per-backend lock script
                backend_config.lock_script = backend_config_data.get("lock_script", "")

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

    # Global lock config (optional, created if relevant env vars are set)
    lock_script = os.environ.get("LLMPROXY_LOCK_SCRIPT", "")
    locked_error = os.environ.get("LLMPROXY_LOCKED_ERROR", "false").lower() == "true"

    if lock_script or locked_error:
        config.global_lock = GlobalLockConfig(
            enabled=True,
            locked_error=locked_error,
            lock_script=lock_script
        )

    # Always set default lock script for get_lock_script_for_backend() compatibility
    # (even if global_lock section not created, scripts won't load without it anyway)
    config.backends_default_lock_script = lock_script
    if not hasattr(config, 'backends_raw') or not config.backends_raw:
        config.backends_raw = {"lock_script": lock_script}

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
