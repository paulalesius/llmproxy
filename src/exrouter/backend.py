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
    - script: Optional path to hook script
    - remapper: Optional path to request remapper script
    """
    name: str
    url: str
    paths: list[str]
    locks: list[str]
    script: Optional[str] = None
    remapper: Optional[str] = None

    def matches_path(self, path: str) -> bool:
        """Check if this backend handles the given path."""
        for pattern in self.paths:
            if "*" in pattern:
                if fnmatch.fnmatch(path, pattern):
                    return True
            else:
                if path == pattern:
                    return True
        return False

    def get_lock_targets(self, all_backends: dict[str, "Backend"]) -> list[str]:
        """Get list of backends this backend locks."""
        return [name for name in self.locks if name in all_backends]
