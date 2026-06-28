#!/usr/bin/env python3
"""LockProxy main entry point."""

import argparse
import asyncio
import sys

from .config import Config
from .proxy import LockProxy


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="LockProxy - Declarative backend proxy with global locking")
    parser.add_argument("--config", "-c", default="config.yaml", help="Path to config file")
    args = parser.parse_args()
    
    try:
        config = Config.from_file(args.config)
        print(f"BLProxy v1.0.0")
        print(f"Backends: {list(config.backends.keys())}")
        print(f"Global locking: {'enabled' if config.global_lock.enabled else 'disabled'}")
        print(f"Starting on {config.server.host}:{config.server.port}")
        
        proxy = LockProxy(config)
        asyncio.run(proxy.run())
        
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
