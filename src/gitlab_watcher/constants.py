"""Constants and configuration defaults for gitlab-watcher."""

# Resource limits and timeouts
MAX_DESCRIPTION_LENGTH = 50000
MAX_TITLE_LENGTH = 255
MAX_BRANCH_LENGTH = 100
MAX_SLUG_LENGTH = 50
SILENCE_TIMEOUT = 1800  # Kill process if no output for 30 minutes
MAX_DOC_CONTENT_LENGTH = 10000  # Maximum combined length of project documentation files
DEFAULT_AI_TOOL_TIMEOUT = 3600 # 1 hour
MAX_TOTAL_PROMPT_LENGTH = 60000 # Overall safety limit for combined AI prompts

# GitLab client defaults
DEFAULT_GITLAB_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 1.0
DEFAULT_POOL_CONNECTIONS = 10
DEFAULT_POOL_MAXSIZE = 20
DEFAULT_CACHE_TTL = 30.0

# AI Tool Error Patterns (broad indicators of tool failure)
AI_TOOL_ERROR_PATTERNS = [
    r"Forbidden",
    r"AI_APICallError",
    r"Authentication failed",
    r"Unauthorized",
    r"Permission denied",
    r"Access denied",
    r"Invalid credentials",
    r"Token expired",
    r"Rate limit exceeded",
    r"Quota exceeded",
    r"Provider returned error",
    r"Service unavailable",
    r"Gateway timeout",
    r"Bad gateway",
    r"Internal server error",
    r"Model not found",
    r"Model overloaded",
    r"Too many requests",
    r"Request timeout",
    r"Connection error",
]

# Broad failure indicators used when the AI tool exits with code 0 but made no
# git changes. These patterns suggest the LLM encountered a problem mid-task
# rather than intentionally deciding no changes were needed.
NO_CHANGES_ERROR_HINTS = [
    r"\bfailed\b",          # "command failed", "task failed"
    r"\bexception\b",       # programming exceptions
    r"\btraceback\b",       # Python tracebacks
    r"\bunable to\b",       # "unable to access / read / write"
    r"\bcould not\b",       # "could not open / find / complete"
    r"\bI cannot\b",        # LLM self-report
    r"\bI can'?t\b",        # LLM self-report
    r"\bnot (?:able|possible) to\b",  # "not able to complete"
    r"\bcrash(?:ed)?\b",    # tool/process crash
]

# Security constants for prompt sanitization
FORBIDDEN_PATTERNS = [
    r"ignore\s+all\s+previous",
    r"system\s+message",
]

__all__ = [
    "MAX_DESCRIPTION_LENGTH",
    "MAX_TITLE_LENGTH",
    "MAX_BRANCH_LENGTH",
    "MAX_SLUG_LENGTH",
    "SILENCE_TIMEOUT",
    "MAX_DOC_CONTENT_LENGTH",
    "DEFAULT_AI_TOOL_TIMEOUT",
    "AI_TOOL_ERROR_PATTERNS",
    "NO_CHANGES_ERROR_HINTS",
    "FORBIDDEN_PATTERNS",
    "DEFAULT_GITLAB_TIMEOUT",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_RETRY_DELAY",
    "DEFAULT_POOL_CONNECTIONS",
    "DEFAULT_POOL_MAXSIZE",
    "DEFAULT_CACHE_TTL",
]
