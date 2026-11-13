"""
tests/conftest.py
==================
Session-scoped fixtures shared across all test modules.
"""
import subprocess
import pytest


def _git_porcelain_set() -> set[str]:
    """Return the set of 'code filename' lines from git status --porcelain."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True, check=False,
    )
    return set(result.stdout.splitlines())


# Capture git working-tree state once at the START of the session, before any
# test runs. The cleanliness test compares against this snapshot so that
# intentional pre-commit edits (Phase 0 fixes, etc.) are not falsely flagged.
_GIT_STATUS_AT_SESSION_START: set[str] = set()


def pytest_sessionstart(session):
    """Called once at the very beginning of the test session."""
    global _GIT_STATUS_AT_SESSION_START
    # Refresh the git index stat-cache BEFORE snapshotting so that files whose
    # on-disk mtime changed (e.g. due to Python writing __pycache__ for this
    # very file) are not falsely reported as "modified" by later git-status calls.
    subprocess.run(
        ["git", "update-index", "--refresh", "-q"],
        capture_output=True, check=False,
    )
    _GIT_STATUS_AT_SESSION_START = _git_porcelain_set()


@pytest.fixture(scope="session")
def git_status_baseline() -> set[str]:
    """Provide the git-porcelain snapshot taken before any test ran."""
    return _GIT_STATUS_AT_SESSION_START

