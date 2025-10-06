import panel as pn
import traceback
import asyncio
from functools import wraps

from .logging import logger

import sys

def only_on_change(*param_names):
    """
    Decorator to avoid re-running a @param.depends-rendered method unless specific parameters have changed.

    This is especially useful for expensive methods that generate plots or views used in Panel,
    and are decorated with @param.depends(..., watch=False). Panel expects these functions to
    always return something (usually a plot), even if the parameters haven't changed.

    Behavior:
    - If any of the specified `param_names` have changed since the last call, the wrapped function is executed.
    - If none of them have changed:
        - If the function was previously called, the last returned result is returned again.
        - If the function has never been called before, returns `None`.

    Limitations:
    - Parameters must be comparable using `!=`.
    - Does not handle in-place mutation detection (e.g. lists or dicts mutated without reassignment).
    - One cache is maintained per instance and per function.

    Example usage:
        @param.depends("x", "y", watch=False)
        @only_on_change("x", "y")
        def my_plot(self):
            return hv.Image(...)  # expensive plot generation
    """

    def decorator(func):
        cache_attr = f"_only_on_change_cache_{func.__name__}"
        result_attr = f"_only_on_change_result_{func.__name__}"

        def wrapper(self, *args, **kwargs):
            # Initialize cache dict if it doesn't exist
            if not hasattr(self, cache_attr):
                setattr(self, cache_attr, {})
            cache = getattr(self, cache_attr)

            # Check if any of the params have changed
            changed = False
            for name in param_names:
                old_val = cache.get(name, object())
                new_val = getattr(self, name)
                if old_val != new_val:
                    changed = True
                    break

            if not changed:
                if hasattr(self, result_attr):
                    logger.debug(f"[{func.__name__}] Skipping (no change), but returning previous value.")
                    return getattr(self, result_attr)
                else:
                    logger.debug(f"[{func.__name__}] Skipping (no change), but no previous return value yet.")
                    return None

            # Update cache with current values
            for name in param_names:
                cache[name] = getattr(self, name)

            # Compute and cache the result
            result = func(self, *args, **kwargs)
            setattr(self, result_attr, result)
            return result

        return wrapper
    return decorator

def catch_and_notify(duration=4000, notification_type="error", prefix=""):
    """
    Decorator to catch exceptions and show a Panel toast notification.

    Args:
        duration (int): Duration of the notification in ms.
        notification_type (str): One of 'info', 'success', 'warning', 'error'.
        prefix (str): Custom (HTML) text to prefix the error message.
    """
    def decorator(func):
        if asyncio.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    msg = f"{prefix}{type(e).__name__}: {str(e)}"
                    getattr(pn.state.notifications, notification_type)(msg, duration=duration)
                    return None
            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    msg = f"{prefix}{type(e).__name__}: {str(e)}"
                    getattr(pn.state.notifications, notification_type)(msg, duration=duration)
                    return None
            return sync_wrapper
    return decorator

def is_running_from_docker():
    """
        Check if the current panel application is running from a docker image
    """
    # Check if panel serve was passed 'from-docker' as one of the --args
    return 'from-docker' in sys.argv

def safe_get(container, *keys, default=None):
    """
    Safely get a nested value from dict-like containers.
    
    Example:
        safe_get(qts, "Width", peak.name, default=None)
    """
    try:
        value = container
        for key in keys:
            value = value[key]
        return value.value
    except Exception:
        return default