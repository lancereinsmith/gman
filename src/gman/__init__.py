"""gman — list, manage, and export your GitHub repositories."""

from gman.client import GitHubClient, GitHubError, RateLimitError
from gman.excel import write_excel

__all__ = [
    "GitHubClient",
    "GitHubError",
    "RateLimitError",
    "__version__",
    "write_excel",
]
__version__ = "0.1.0"
