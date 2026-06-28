"""Configuration loading from YAML."""

from dataclasses import dataclass, field
from typing import Optional
import yaml


@dataclass
class BackendConfig:
    """Backend configuration from YAML."""
    url: str
    paths: list[str]
    locks: list[str] = field(default_factory=list)


@dataclass
class GlobalLockConfig:
    """Global lock settings."""
    enabled: bool = True
    timeout: int = 300


@dataclass
class ServerConfig:
    """Server configuration."""
    host: str = "0.0.0.0"
    port: int = 4001


@dataclass
class Config:
    """Full proxy configuration."""
    server: ServerConfig = field(default_factory=ServerConfig)
    backends: dict[str, BackendConfig] = field(default_factory=dict)
    global_lock: GlobalLockConfig = field(default_factory=GlobalLockConfig)

    @classmethod
    def from_file(cls, path: str) -> "Config":
        """Load config from YAML file."""
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """Load config from dict."""
        config = cls()
        
        # Server config
        if "server" in data:
            server_data = data["server"]
            config.server = ServerConfig(
                host=server_data.get("host", "0.0.0.0"),
                port=server_data.get("port", 4001)
            )
        
        # Backends
        if "backends" in data:
            for name, backend_data in data["backends"].items():
                config.backends[name] = BackendConfig(
                    url=backend_data["url"],
                    paths=backend_data.get("paths", []),
                    locks=backend_data.get("locks", [])
                )
        
        # Global lock config
        if "global_lock" in data:
            lock_data = data["global_lock"]
            config.global_lock = GlobalLockConfig(
                enabled=lock_data.get("enabled", True),
                timeout=lock_data.get("timeout", 300)
            )
        
        return config
