#!/usr/bin/env python3
"""
Retry policies with exponential backoff.

Usage:
    from execution.core.retry import retry_with_backoff
    
    @retry_with_backoff(max_attempts=3, base_delay=1.0)
    def call_api():
        # ... API call that might fail
        pass
"""

import time
import functools
from typing import Callable, Optional, Type, Tuple, Any


def retry_with_backoff(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable[[Exception, int], None]] = None
):
    """
    Decorator for retrying functions with exponential backoff.
    
    Args:
        max_attempts: Maximum number of attempts (including first try)
        base_delay: Initial delay in seconds
        max_delay: Maximum delay between retries
        exponential_base: Base for exponential backoff (delay = base_delay * exponential_base^attempt)
        exceptions: Tuple of exception types to catch and retry
        on_retry: Optional callback function(exception, attempt) called on each retry
    
    Returns:
        Decorated function with retry logic
    
    Example:
        @retry_with_backoff(max_attempts=3, base_delay=2.0)
        def fetch_data():
            return requests.get("https://api.example.com/data")
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    
                    if attempt == max_attempts:
                        # Last attempt failed, raise the exception
                        raise
                    
                    # Calculate delay with exponential backoff
                    delay = min(
                        base_delay * (exponential_base ** (attempt - 1)),
                        max_delay
                    )
                    
                    # Call retry callback if provided
                    if on_retry:
                        on_retry(e, attempt)
                    
                    print(f"[RETRY] Attempt {attempt}/{max_attempts} failed: {e}")
                    print(f"[RETRY] Waiting {delay:.1f}s before next attempt...")
                    
                    time.sleep(delay)
            
            # Should not reach here, but just in case
            raise last_exception
        
        return wrapper
    return decorator


class RetryPolicy:
    """Configurable retry policy for use in workflows."""
    
    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0
    ):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
    
    def execute(self, func: Callable, *args, **kwargs) -> Any:
        """Execute a function with this retry policy."""
        @retry_with_backoff(
            max_attempts=self.max_attempts,
            base_delay=self.base_delay,
            max_delay=self.max_delay,
            exponential_base=self.exponential_base
        )
        def wrapped():
            return func(*args, **kwargs)
        
        return wrapped()
    
    def to_dict(self) -> dict:
        """Serialize policy to dict."""
        return {
            "max_attempts": self.max_attempts,
            "base_delay": self.base_delay,
            "max_delay": self.max_delay,
            "exponential_base": self.exponential_base
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "RetryPolicy":
        """Create policy from dict."""
        return cls(**data)
