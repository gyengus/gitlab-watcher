"""GitLab Watcher - Monitor projects and process issues/MRs automatically."""

import warnings

try:
    # Suppress the specific urllib3 dependency warning
    # This warning is harmless and doesn't affect functionality
    warnings.filterwarnings("ignore", message=".*urllib3.*chardet.*charset-normalizer.*")
except Exception:
    # Fallback if the specific warning message changes or is not found
    pass

try:
    from ._version import __version__
except ImportError:
    # Fallback for local development without setuptools_scm installed
    __version__ = "0.0.0+unknown"
