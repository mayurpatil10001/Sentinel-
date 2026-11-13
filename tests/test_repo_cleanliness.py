"""
tests/test_repo_cleanliness.py
================================
Assert that running the full test suite does not modify any git-tracked file.

This catches the class of bug where production code writes to a path inside
the repository tree at import-time or during test execution, dirtying
`git status --porcelain` and causing noise in diffs.

HOW IT WORKS
  At session start, conftest.py records git-status --porcelain.
  This test compares the CURRENT status against that snapshot.
  Only NEW modifications introduced DURING the test run are flagged.
  Pre-existing uncommitted edits (e.g. Phase 0 fixes not yet committed)
  are excluded from the assertion.

  Run this test LAST (or run the full suite with this file included) to
  get the strongest guarantee.
"""
import subprocess
import pytest


def _git_porcelain_set() -> set[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True, check=False,
    )
    return set(result.stdout.splitlines())


def _only_tracked_modifications(lines: set[str]) -> list[str]:
    """Filter to lines that represent modifications to TRACKED files only."""
    result = []
    for line in sorted(lines):
        if not line.strip():
            continue
        code = line[:2]
        filename = line[3:].strip()
        # M in column 1 = staged; M in column 2 = unstaged working-tree change
        # We want unstaged changes to tracked files: ' M' or 'MM' or 'AM'
        # Exclude untracked '??' — tests may legitimately create new files
        # in untracked/gitignored locations.
        if "?" not in code and filename:
            result.append(filename)
    return result


class TestRepoCleanlinessAfterTests:
    """Running the test suite must not dirty any tracked file."""

    def test_no_new_tracked_files_modified_by_tests(self, git_status_baseline):
        """
        Verify that no git-tracked file has been NEWLY modified during this
        test run (modifications that existed before the session started are
        excluded from the assertion).

        If this test fails, a previous test in this session wrote to a
        tracked file. Find the culprit with:
            git diff --name-only
        and redirect that write to tmp_path or to the gitignored runtime/
        directory.

        Current known-good state: app/harm_estimation/guard_audit.log is
        redirected to runtime/ via SENTINEL_AUDIT_LOG; backtest calibration
        json is untracked.
        """
        before = set(git_status_baseline)
        after  = _git_porcelain_set()

        new_dirtied = after - before
        # Only care about modifications to tracked files
        new_tracked_mods = _only_tracked_modifications(new_dirtied)

        assert new_tracked_mods == [], (
            "The following tracked files were NEWLY modified during this "
            "test run (they were clean at session start):\n"
            + "\n".join(f"  {f}" for f in new_tracked_mods)
            + "\n\nThis means a test (or code it exercises) writes to a path "
            "inside the repository tree. Redirect those writes to tmp_path "
            "or to the gitignored runtime/ directory.\n"
            "Hint: run `git diff --name-only` after a test run to see which "
            "files changed."
        )
