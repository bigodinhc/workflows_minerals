#!/usr/bin/env python3
"""
Structured logging for workflow execution.

Usage:
    from execution.core.logger import WorkflowLogger
    
    logger = WorkflowLogger("my_workflow")
    logger.info("Processing started", {"items": 10})
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
import uuid


class WorkflowLogger:
    """Structured logger for workflow execution with JSON output."""
    
    LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    
    def __init__(self, workflow: str, run_id: Optional[str] = None):
        """
        Initialize logger for a workflow.
        
        Args:
            workflow: Name of the workflow/directive
            run_id: Optional run ID (auto-generated if not provided)
        """
        self.workflow = workflow
        self.run_id = run_id or str(uuid.uuid4())[:8]
        self.step = "init"
        self._setup_log_file()
    
    def _setup_log_file(self):
        """Create log directory and file."""
        log_dir = Path(".tmp/logs") / self.workflow
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = log_dir / f"{self.run_id}.json"
        self._logs = []
    
    def set_step(self, step: str):
        """Set the current step name for logging context."""
        self.step = step
    
    def _log(self, level: str, message: str, data: Optional[dict] = None):
        """Internal logging method."""
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "workflow": self.workflow,
            "run_id": self.run_id,
            "step": self.step,
            "level": level,
            "message": message,
            "data": data or {}
        }
        self._logs.append(entry)
        self._write_logs()
        
        # Also print to console for visibility
        print(f"[{level}] {self.workflow}/{self.step}: {message}")
    
    def _write_logs(self):
        """Write logs to file."""
        with open(self.log_file, "w") as f:
            json.dump(self._logs, f, indent=2)
    
    def debug(self, message: str, data: Optional[dict] = None):
        """Log debug message."""
        self._log("DEBUG", message, data)
    
    def info(self, message: str, data: Optional[dict] = None):
        """Log info message."""
        self._log("INFO", message, data)
    
    def warning(self, message: str, data: Optional[dict] = None):
        """Log warning message."""
        self._log("WARNING", message, data)
    
    def error(self, message: str, data: Optional[dict] = None):
        """Log error message."""
        self._log("ERROR", message, data)
    
    def critical(self, message: str, data: Optional[dict] = None):
        """Log critical message."""
        self._log("CRITICAL", message, data)
    
    def get_logs(self) -> list:
        """Return all logs for this run."""
        return self._logs
