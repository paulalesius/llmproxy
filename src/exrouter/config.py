"""Configuration loading from YAML with Pydantic validation."""

from pydantic import BaseModel, Field, AnyHttpUrl, field_validator, model_validator
from typing import Any, Optional
import yaml


class BackendConfig(BaseModel):
    """Backend configuration from YAML."""
    url: AnyHttpUrl = Field(..., description="Backend server URL (must be http/https)")
    paths: list[str] = Field(default_factory=list, description="Path patterns this backend handles")
    locks: list[str] = Field(default_factory=list, description="Other backends to lock while processing")
    script: Optional[str] = Field(default=None, description="Path to Python hook script")

    @field_validator('paths', 'locks', mode='before')
    @classmethod
    def ensure_list(cls, v: Any) -> list[str]:
        return v or []


class GlobalLockConfig(BaseModel):
    """Global lock settings."""
    enabled: bool = Field(default=True, description="Enable global locking")
    timeout: int = Field(default=300, gt=0, description="Timeout in seconds when waiting for locks")


class ServerConfig(BaseModel):
    """Server configuration."""
    host: str = Field(default="0.0.0.0", description="Host to bind to")
    port: int = Field(default=4001, ge=1, le=65535, description="Port to listen on")


class Config(BaseModel):
    """Full proxy configuration with validation."""
    server: ServerConfig = Field(default_factory=ServerConfig)
    backends: dict[str, BackendConfig] = Field(default_factory=dict)
    global_lock: GlobalLockConfig = Field(default_factory=GlobalLockConfig)

    @model_validator(mode='after')
    def validate_lock_targets_exist(self) -> "Config":
        """Ensure that all lock targets actually exist as backends."""
        backend_names = set(self.backends.keys())
        for name, backend in self.backends.items():
            for lock in backend.locks:
                if lock not in backend_names:
                    raise ValueError(
                        f"Backend '{name}' tries to lock '{lock}', "
                        f"but no backend named '{lock}' exists."
                    )
        return self

    @classmethod
    def from_file(cls, path: str) -> "Config":
        """Load config from YAML file with validation."""
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return cls.model_validate(data)

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        """Load config from dict with validation."""
        return cls.model_validate(data or {})
