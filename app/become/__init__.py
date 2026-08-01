"""BECOME.AC transport integration boundary.

I1 exposes only a package/preflight seam. Provider execution remains disabled.
"""

from .runtime_preflight import build_runtime_preflight

__all__ = ["build_runtime_preflight"]
