"""Backend component - represents a single backend with paths and locks."""

from dataclasses import dataclass
from typing import Optional
import fnmatch


@dataclass
class Backend:
    """A backend component.
    
    Holds:
    - name: Backend identifier
    - url: Backend server URL
    - paths: List of path patterns this backend handles
    - locks: List of other backend names to lock while processing
    """
    name: str
    url: str
    paths: list[str]
    locks: list[str]

    def matches_path(self, path: str) -> bool:
        """Check if this backend handles the given path."""
        for pattern in self.paths:
            # Handle wildcards
            if "*" in pattern:
                # Convert /v1/vision/* to fnmatch pattern
                if fnmatch.fnmatch(path, pattern):
                    return True
            else:
                if path == pattern:
                    return True
        return False

    def get_lock_targets(self, all_backends: dict[str, "Backend"]) -> list[str]:
        """Get list of backends this backend locks."""
        return [name for name in self.locks if name in all_backends]
