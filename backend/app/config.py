"""Application Configuration Module Alias for backend/app/config.py."""
import sys
from pathlib import Path

# Add project root to sys.path if not present
root_dir = str(Path(__file__).resolve().parent.parent.parent)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

try:
    from app.core.config import Settings, settings
except ImportError:
    from backend.app.core.config import Settings, settings

__all__ = ["Settings", "settings"]
