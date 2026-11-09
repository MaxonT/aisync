"""pytest setup for ai-sync tests: git hook variables are removed before any test (see git_env.py)."""
import git_env  # noqa: F401  (scrubs os.environ on import)
