#!/usr/bin/env python3
"""
State management for workflow persistence.

Usage:
    from execution.core.state import StateManager
    
    state = StateManager("my_workflow")
    state.set("last_cursor", "abc123")
    cursor = state.get("last_cursor")
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


class StateManager:
    """Manage persistent state for workflows."""
    
    def __init__(self, workflow: str, state_dir: str = ".state"):
        """
        Initialize state manager for a workflow.
        
        Args:
            workflow: Name of the workflow/directive
            state_dir: Directory for state files
        """
        self.workflow = workflow
        self.state_dir = Path(state_dir)
        self.state_file = self.state_dir / f"{workflow}.json"
        self._state = self._load()
    
    def _load(self) -> dict:
        """Load state from file."""
        if self.state_file.exists():
            with open(self.state_file, "r") as f:
                return json.load(f)
        return {"_meta": {"created": datetime.utcnow().isoformat()}}
    
    def _save(self):
        """Save state to file."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._state["_meta"]["updated"] = datetime.utcnow().isoformat()
        with open(self.state_file, "w") as f:
            json.dump(self._state, f, indent=2, default=str)
    
    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a value from state.
        
        Args:
            key: Key to retrieve
            default: Default value if key not found
        
        Returns:
            The stored value or default
        """
        return self._state.get(key, default)
    
    def set(self, key: str, value: Any) -> None:
        """
        Set a value in state and persist.
        
        Args:
            key: Key to set
            value: Value to store
        """
        self._state[key] = value
        self._save()
    
    def delete(self, key: str) -> None:
        """
        Delete a key from state.
        
        Args:
            key: Key to delete
        """
        if key in self._state:
            del self._state[key]
            self._save()
    
    def clear(self) -> None:
        """Clear all state except metadata."""
        self._state = {"_meta": self._state.get("_meta", {})}
        self._save()
    
    def all(self) -> dict:
        """Return all state data."""
        return {k: v for k, v in self._state.items() if not k.startswith("_")}
    
    def has(self, key: str) -> bool:
        """Check if a key exists in state."""
        return key in self._state


class RunContext:
    """In-memory context for a single workflow run."""
    
    def __init__(self, inputs: Optional[dict] = None):
        """
        Initialize run context.
        
        Args:
            inputs: Initial input data for the run
        """
        self._data = inputs or {}
        self._outputs = {}
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get value from context."""
        return self._data.get(key, default)
    
    def set(self, key: str, value: Any) -> None:
        """Set value in context."""
        self._data[key] = value
    
    def set_output(self, key: str, value: Any) -> None:
        """Set an output value."""
        self._outputs[key] = value
    
    def get_outputs(self) -> dict:
        """Get all outputs."""
        return self._outputs
    
    def all(self) -> dict:
        """Return all context data."""
        return self._data.copy()
