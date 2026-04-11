import logging
import pytest
import threading

@pytest.fixture(autouse=True)
def cleanup_logging():
    """Cleanup root logger between tests to prevent handler/filter accumulation."""
    # Capture original handlers/filters
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_filters = list(root_logger.filters)
    
    yield
    
    # After test, restore root logger to original state
    for handler in list(root_logger.handlers):
        if handler not in original_handlers:
            root_logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass
                
    for filt in list(root_logger.filters):
        if filt not in original_filters:
            root_logger.removeFilter(filt)

@pytest.fixture(autouse=True)
def cleanup_threads():
    """Monitor for leaked threads between tests."""
    initial_threads = threading.enumerate()
    
    yield
    
    # We don't necessarily kill threads here because they might be daemon 
    # and eventually exit, but we could log if they persist.
    # The active fixes in code (stopping timers) should handle most cases.
