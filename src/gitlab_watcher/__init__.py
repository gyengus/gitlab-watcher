"""GitLab Watcher - Monitor projects and process issues/MRs automatically."""

import warnings

# Suppress the specific urllib3 dependency warning
# This warning is harmless and doesn't affect functionality
try:
    from requests.packages.urllib3.exceptions import DependencyWarning
    warnings.filterwarnings("ignore", category=DependencyWarning)
except (ImportError, AttributeError):
    try:
        from urllib3.exceptions import DependencyWarning
        warnings.filterwarnings("ignore", category=DependencyWarning)
    except (ImportError, AttributeError):
        # Fallback if we can't find the specific class or requests.packages is missing
        warnings.filterwarnings("ignore", message=".*urllib3.*chardet.*charset-normalizer.*")

__version__ = "1.0.0"
