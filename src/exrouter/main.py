#!/usr/bin/env python3
"""EXRouter main entry point."""

import argparse
import asyncio
import logging
import sys

from .config import Config
from .proxy import LockProxy


def setup_logging() -> None:
    """Configure logging for EXRouter."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Ensure our logger is at INFO level
    logging.getLogger("exrouter").setLevel(logging.INFO)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="EXRouter - Declarative backend proxy with global locking")
    parser.add_argument("--config", "-c", default="config.yaml", required=True, help="Path to config file")
    args = parser.parse_args()
    
    try:
        setup_logging()
        logger = logging.getLogger("exrouter")
        
        config = Config.from_file(args.config)
        logger.info("EXRouter v1.0.0 starting up")
        logger.info(f"Backends: {list(config.backends.keys())}")
        logger.info(f"Global locking: {'enabled' if config.global_lock.enabled else 'disabled'}")
        logger.info(f"Starting on {config.server.host}:{config.server.port}")
        
        proxy = LockProxy(config)
        asyncio.run(proxy.run())
        
    except Exception as e:
        logger = logging.getLogger("exrouter")
        logger.error(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
