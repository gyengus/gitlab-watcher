import logging
import pytest
import threading
from unittest.mock import patch, MagicMock


class MockFileHandler(logging.Handler):
    """Mock file handler that doesn't actually write to files."""
    def __init__(self, filename=None, *args, **kwargs):
        super().__init__()
        self.baseFilename = filename or "/dev/null"
        self.level = kwargs.get('level', logging.NOTSET)
        self.formatter = None
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def close(self):
        self.records.clear()
        super().close()


class FakeThread:
    """A Thread substitute that runs the target synchronously in the calling thread.

    Prevents real thread creation in processor tests that exercise
    ``_run_ai_tool``, which spawns a reader thread for subprocess stdout.
    """

    def __init__(self, target=None, args=(), kwargs=None, name=None, daemon=None, **_kw):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}
        self.name = name or ""
        self.daemon = daemon or False

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)

    def join(self, timeout=None):
        pass

    def is_alive(self):
        return False


@pytest.fixture(autouse=True)
def cleanup_logging():
    """Cleanup root logger between tests to prevent handler/filter accumulation."""
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_filters = list(root_logger.filters)

    yield

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
def mock_file_handler_in_tests():
    """Replace FileHandler with MockFileHandler in tests to avoid actual file writes."""
    with patch('logging.FileHandler', MockFileHandler):
        yield


@pytest.fixture(autouse=True)
def patch_threading_in_processor_tests():
    """Patch the Thread class used in processor._run_ai_tool to prevent real thread leaks.

    Patches only the _thread_cls reference in the processor module, not the global
    threading.Thread, so StateManager's threading.Timer continues to work normally.
    """
    import gitlab_watcher.processor as proc_mod
    original = proc_mod._thread_cls
    proc_mod._thread_cls = FakeThread
    yield
    proc_mod._thread_cls = original