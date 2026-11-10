"""
tests/test_security_secrets.py
================================
Checks that no tracked file in the repository contains hardcoded credentials
or sensitive IDs. Fails the build (and therefore CI) if any are found.

What is checked:
  - The old Fyers secret (O06AUI9GFX) must not appear in any tracked file.
  - The Fyers App ID (S08D2750W7) must not appear in source/template files
    (it may appear in .env.example ONLY as a placeholder comment, never as
    an actual value that would be used at runtime).
  - The broker client/account ID (FAA25578) must not appear anywhere in
    tracked files. The UI derives any display name from /api/live/status
    at runtime.

If you rotate the secret and the old value still appears in a file, this
test catches it before it is committed (via pre-commit) and before CI merges.

HOW TO RUN:
    pytest tests/test_security_secrets.py -v

NOTE: This test checks the working tree, not git history. For history,
see the Phase 0 instructions in the README for running git filter-repo.
"""

import pathlib
import re
import subprocess
import sys

# ---------------------------------------------------------------------------
# Patterns that must NEVER appear in any tracked source file
# ---------------------------------------------------------------------------
FORBIDDEN = [
    # Old Fyers secret — rotate this in the developer portal immediately
    # if you are reading this and the secret is still active.
    ("FYERS_SECRET_VALUE", re.compile(r"O06AUI9GFX")),
    # Fyers App ID as a literal runtime value (not a placeholder comment)
    # Allowed ONLY in .env.example as a placeholder string
    ("FYERS_APP_ID_VALUE", re.compile(r"S08D2750W7-100")),
    # Broker account / client ID — must never appear in source or HTML
    ("BROKER_CLIENT_ID", re.compile(r"FAA25578")),
]

# Files where the pattern is allowed (e.g. .env.example placeholders)
ALLOWLIST: dict[str, list[str]] = {
    "FYERS_APP_ID_VALUE": [".env.example"],  # only as a placeholder comment
}

# File extensions to scan (source, templates, configs, scripts)
SCAN_EXTENSIONS = {
    ".py", ".html", ".js", ".ts", ".json", ".yaml", ".yml",
    ".toml", ".cfg", ".ini", ".txt", ".md", ".bat", ".sh",
    ".env.example",
}

REPO_ROOT = pathlib.Path(__file__).parent.parent


def _get_tracked_files() -> list[pathlib.Path]:
    """Return list of files currently tracked by git."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return [REPO_ROOT / f.strip() for f in result.stdout.splitlines() if f.strip()]


def test_no_hardcoded_credentials():
    """No tracked file contains any forbidden credential pattern."""
    tracked = _get_tracked_files()
    violations = []

    for path in tracked:
        if path.suffix not in SCAN_EXTENSIONS and path.name not in {"Makefile"}:
            continue
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        relative = path.relative_to(REPO_ROOT)

        for label, pattern in FORBIDDEN:
            allowed_files = ALLOWLIST.get(label, [])
            if str(relative).replace("\\", "/") in allowed_files:
                continue
            # For .env.example, only allow it if it's clearly a placeholder comment
            if relative.name == ".env.example" and label == "FYERS_APP_ID_VALUE":
                # Allowed ONLY in comment lines
                bad_lines = [
                    line for line in content.splitlines()
                    if pattern.search(line) and not line.strip().startswith("#")
                ]
                if bad_lines:
                    violations.append((relative, label, bad_lines))
                continue

            matches = pattern.findall(content)
            if matches:
                violations.append((relative, label, matches[:3]))

    if violations:
        lines = ["\nHardcoded credential violation(s) found:\n"]
        for path, label, matches in violations:
            lines.append(f"  {path}  [{label}]  found: {matches}")
        lines.append(
            "\nAction required:"
            "\n  1. Remove the hardcoded value and load it from env vars instead."
            "\n  2. If this is git history, run git filter-repo to purge it."
            "\n  3. Rotate any exposed secrets immediately in the Fyers developer portal."
        )
        raise AssertionError("\n".join(lines))


def test_no_default_green_badge():
    """
    The dashboard HTML must not contain a hardcoded 'CONNECTED' status.
    The Fyers badge must be populated dynamically from /api/live/status.
    """
    dashboard = REPO_ROOT / "dashboard" / "static" / "index.html"
    if not dashboard.exists():
        return  # dashboard not built yet

    content = dashboard.read_text(encoding="utf-8", errors="replace")

    # The literal string "CONNECTED (FAA" is the hardcoded badge text we removed.
    # After Phase 0, it must not appear anywhere in the HTML.
    assert "CONNECTED (FAA" not in content, (
        "dashboard/static/index.html contains a hardcoded 'CONNECTED (FAA...)' badge. "
        "The Fyers connection status must be populated dynamically from /api/live/status."
    )

    # The badge element must exist so JS can populate it
    assert 'id="fyersBadge"' in content, (
        "dashboard/static/index.html is missing the fyersBadge element. "
        "The JS function fyersPollStatus() needs it to display connection state."
    )
