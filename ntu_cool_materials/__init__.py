"""NTU COOL material sync tools."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _pkg_version

# Single source of truth is pyproject.toml; read it back from the installed
# package metadata so __version__ can never drift from what was shipped.
# Falls back only when running from a source tree that was never installed
# (not even `pip install -e .`).
try:
    __version__ = _pkg_version("get-class-material")
except PackageNotFoundError:  # pragma: no cover - source-tree-without-install
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
