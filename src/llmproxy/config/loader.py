"""Configuration loader - YAML only (authoritative)."""

import os
import yaml
from typing import Optional, Dict, Any
from .models import AppConfig, ServerConfig, BackendConfig, LockConfig


def load_yaml_config(path: Optional[str] = None) -> Dict[str, Any]:
    """Load raw config from YAML file or return empty dict."""
    if path and os.path.exists(path):
        with open(path, "r") as f:
            return yaml.safe_load(f) or {}
    # Try default locations
    for candidate in ["config.yaml", "src/llmproxy/config.yaml", "/etc/llmproxy/config.yaml"]:
        if os.path.exists(candidate):
            with open(candidate, "r") as f:
                return yaml.safe_load(f) or {}
    return {}


def build_app_config(raw: Dict[str, Any]) -> AppConfig:
    """Build typed AppConfig from raw dict. Very tolerant."""
    server_raw = raw.get("server", {}) or {}

    server = ServerConfig(
        host=server_raw.get("host", "0.0.0.0"),
        port=int(server_raw.get("port", 8000)),
        log_level=server_raw.get("log_level", "INFO"),
    )

    # --- Backends ---
    backends: Dict[str, BackendConfig] = {}
    backends_raw = raw.get("backends", {}) or {}

    # Support both styles:
    # backends:
    #   rerank:
    #     base_url: ...
    #   llm:
    #     ...
    for name in ["llm", "embed", "rerank", "embeddings"]:
        entry = backends_raw.get(name) or backends_raw.get(name.replace("embeddings", "embed"))
        if isinstance(entry, dict):
            url = entry.get("base_url") or entry.get("url") or entry.get("baseURL") or ""
            if not url and name == "rerank":
                url = "http://127.0.0.1:8082"
            elif not url and name == "llm":
                url = "http://127.0.0.1:8080"
            elif not url and name in ("embed", "embeddings"):
                url = "http://127.0.0.1:8081"

            backends[name if name != "embeddings" else "embed"] = BackendConfig(
                name=name if name != "embeddings" else "embed",
                url=url,
                timeout=int(entry.get("timeout", 30)),
                read_timeout=int(entry.get("read_timeout", entry.get("readTimeout", 60))),
                locks=entry.get("locks", []) or [],
                enabled=entry.get("enabled", True),
                lock_script=entry.get("lock_script"),
            )

    # Ensure we always have the three main backends
    for name, default_url in [("llm", "http://127.0.0.1:8080"),
                              ("embed", "http://127.0.0.1:8081"),
                              ("rerank", "http://127.0.0.1:8082")]:
        if name not in backends:
            backends[name] = BackendConfig(name=name, url=default_url)

    # --- Lock config ---
    # Build lock.backends directly from per-backend locks lists
    # config.yaml format: backends.llm.locks: [embed, rerank]
    # Means: when llm backend is accessed, acquire locks for embed and rerank
    lock_backends_map: dict[str, list[str]] = {}
    for backend_name, backend_config in backends.items():
        # Direct mapping: backend_name -> list of backends it locks
        if backend_config.locks:
            lock_backends_map[backend_name] = list(backend_config.locks)
    
    lock_raw = raw.get("global_lock") or raw.get("lock") or backends_raw.get("global_lock", {})
    if isinstance(lock_raw, bool):
        lock_raw = {"enabled": lock_raw}
    
    # Merge raw config with computed map (raw takes precedence if present)
    raw_backends = lock_raw.get("backends", {}) or {}
    for key, value in lock_backends_map.items():
        if key not in raw_backends:
            raw_backends[key] = value
    
    lock = LockConfig(
        enabled=bool(lock_raw.get("enabled", True)),
        locked_error=bool(lock_raw.get("locked_error", False)),
        backends=raw_backends,
        lock_script=lock_raw.get("lock_script"),
    )

    api_key = raw.get("api_key") or server_raw.get("api_key") or ""

    return AppConfig(
        backends=backends,
        server=server,
        lock=lock,
        api_key=api_key,
        log_requests=raw.get("log_requests", True),
        log_responses=raw.get("log_responses", True),
    )


def load_config(path: Optional[str] = None) -> AppConfig:
    raw = load_yaml_config(path)
    return build_app_config(raw)


def reload_config(path: Optional[str] = None) -> AppConfig:
    from .state import set_config
    cfg = load_config(path)
    set_config(cfg)
    return cfg
