#!/usr/bin/env python3
"""
Workflow execution engine.

This module provides the core runner for executing workflows defined in directives.

Usage:
    from execution.core.runner import run_workflow
    
    result = run_workflow("my_directive", {"input": "value"})
"""

import os
import sys
from pathlib import Path
from typing import Any, Optional, Callable

from .logger import WorkflowLogger
from .state import StateManager, RunContext
from .retry import RetryPolicy


def run_workflow(
    directive: str,
    inputs: Optional[dict] = None,
    parent_run_id: Optional[str] = None
) -> dict:
    """
    Execute a workflow defined by a directive.
    
    This is the main entry point for running workflows. The agent reads the
    directive, interprets the steps, and uses this function to track execution.
    
    Args:
        directive: Name of the directive (without path/extension)
        inputs: Input data for the workflow
        parent_run_id: Run ID of parent workflow (for sub-workflows)
    
    Returns:
        dict with 'success', 'outputs', and 'logs'
    
    Example:
        result = run_workflow("scrape_website", {"url": "https://example.com"})
        if result["success"]:
            print(result["outputs"])
    """
    # Initialize components
    logger = WorkflowLogger(directive)
    state = StateManager(directive)
    context = RunContext(inputs)
    
    logger.info("Workflow started", {
        "inputs": inputs,
        "parent_run_id": parent_run_id
    })
    
    try:
        # The actual step execution is done by the LLM agent
        # This function provides the infrastructure
        
        # Return structure for agent to populate
        return {
            "success": True,
            "run_id": logger.run_id,
            "outputs": context.get_outputs(),
            "logs": logger.get_logs(),
            "state": state.all(),
            "context": context.all()
        }
        
    except Exception as e:
        logger.critical("Workflow failed", {"error": str(e)})
        return {
            "success": False,
            "run_id": logger.run_id,
            "error": str(e),
            "logs": logger.get_logs()
        }


def create_step_executor(
    logger: WorkflowLogger,
    context: RunContext,
    retry_policy: Optional[RetryPolicy] = None
) -> Callable:
    """
    Create a step executor with logging and retry support.
    
    Args:
        logger: WorkflowLogger instance
        context: RunContext instance
        retry_policy: Optional retry policy
    
    Returns:
        Callable that executes steps with proper handling
    """
    def execute_step(
        step_name: str,
        func: Callable,
        *args,
        **kwargs
    ) -> Any:
        logger.set_step(step_name)
        logger.info(f"Step started: {step_name}")
        
        try:
            if retry_policy:
                result = retry_policy.execute(func, *args, **kwargs)
            else:
                result = func(*args, **kwargs)
            
            logger.info(f"Step completed: {step_name}", {"result_type": type(result).__name__})
            return result
            
        except Exception as e:
            logger.error(f"Step failed: {step_name}", {"error": str(e)})
            raise
    
    return execute_step


# Convenience exports
__all__ = ["run_workflow", "create_step_executor"]
