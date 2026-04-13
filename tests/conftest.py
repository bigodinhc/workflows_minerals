"""Shared pytest fixtures."""
import sys
from pathlib import Path

# Add repo root to sys.path so tests can import execution.* modules
sys.path.insert(0, str(Path(__file__).parent.parent))
