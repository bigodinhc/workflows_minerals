"""Shared pytest fixtures."""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
# Add repo root to sys.path so tests can import execution.* modules
sys.path.insert(0, str(_REPO_ROOT))
# Add webhook/ so bare imports (`import redis_queries`) resolve the same way
# they do in production (Dockerfile copies webhook/ contents to /app/).
sys.path.insert(0, str(_REPO_ROOT / "webhook"))
