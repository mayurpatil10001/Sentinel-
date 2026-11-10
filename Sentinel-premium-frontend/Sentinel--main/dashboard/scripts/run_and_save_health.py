"""
dashboard/scripts/run_and_save_health.py
========================================
Run the real test suite + stress tests and persist results to
dashboard/data/engineering_health.json so the API can serve real numbers.

Usage (from repo root):
    python dashboard/scripts/run_and_save_health.py

This script must be run manually (or in CI) before the Engineering Health
page shows real data. It NEVER fabricates numbers.
"""
import json
import pathlib
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).parent.parent.parent
OUT  = ROOT / "dashboard" / "data" / "engineering_health.json"


def run(cmd: list[str], cwd=None) -> dict:
    """Run a command, return {returncode, stdout, stderr, duration_s}."""
    t0 = time.monotonic()
    result = subprocess.run(
        cmd, capture_output=True, text=True, cwd=cwd or ROOT
    )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout[-8000:] if result.stdout else "",  # last 8KB
        "stderr": result.stderr[-2000:] if result.stderr else "",
        "duration_s": round(time.monotonic() - t0, 2),
    }


def parse_pytest_summary(stdout: str) -> dict:
    """
    Parse pytest's summary line: '273 passed, 4 deselected in 170.36s' etc.
    Returns {passed, failed, errors, duration_s}.
    """
    import re
    passed_m = re.search(r'(\d+)\s+passed', stdout)
    failed_m = re.search(r'(\d+)\s+failed', stdout)
    errors_m = re.search(r'(\d+)\s+error', stdout)
    dur_m = re.search(r'in\s+([\d.]+)s', stdout)

    return {
        "passed": int(passed_m.group(1)) if passed_m else 0,
        "failed": int(failed_m.group(1)) if failed_m else 0,
        "errors": int(errors_m.group(1)) if errors_m else 0,
        "duration_s": float(dur_m.group(1)) if dur_m else None,
    }


def main():
    print("=" * 60)
    print("Sentinel Engineering Health — Real Run")
    print("=" * 60)

    health = {
        "recorded_at": datetime.now(tz=timezone.utc).isoformat(),
        "runs": {},
    }

    # ── 1. pytest full suite ────────────────────────────────────────────────
    print("\n[1/3] Running pytest tests/ ...")
    r = run([sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short", "-q",
             "--ignore=tests/__pycache__"])
    summary = parse_pytest_summary(r["stdout"] + r["stderr"])
    health["runs"]["pytest"] = {
        "command": "python -m pytest tests/ -q",
        "returncode": r["returncode"],
        "duration_s": r["duration_s"],
        **summary,
        "raw_tail": (r["stdout"] + r["stderr"])[-1500:],
    }
    status = "PASS" if r["returncode"] == 0 else "FAIL"
    print(f"  {status}: {summary.get('passed')} passed, {summary.get('failed')} failed in {r['duration_s']}s")

    # ── 2. Ingestion volume stress test (if it exists) ──────────────────────
    stress_file = ROOT / "stress" / "test_ingestion_volume.py"
    if stress_file.exists():
        print("\n[2/3] Running ingestion volume stress test ...")
        r2 = run([sys.executable, str(stress_file)])
        health["runs"]["stress_ingestion"] = {
            "command": f"python {stress_file.name}",
            "returncode": r2["returncode"],
            "duration_s": r2["duration_s"],
            "raw_tail": (r2["stdout"] + r2["stderr"])[-1500:],
        }
        print(f"  Done in {r2['duration_s']}s (rc={r2['returncode']})")
    else:
        health["runs"]["stress_ingestion"] = {
            "status": "file_not_found",
            "note": "stress/test_ingestion_volume.py not found — skipped",
        }
        print("\n[2/3] stress/test_ingestion_volume.py not found — skipped")

    # ── 3. Concurrent access stress test ────────────────────────────────────
    conc_file = ROOT / "stress" / "test_concurrent_access.py"
    if conc_file.exists():
        print("\n[3/3] Running concurrent access stress test ...")
        r3 = run([sys.executable, str(conc_file)])
        health["runs"]["stress_concurrent"] = {
            "command": f"python {conc_file.name}",
            "returncode": r3["returncode"],
            "duration_s": r3["duration_s"],
            "raw_tail": (r3["stdout"] + r3["stderr"])[-1500:],
        }
        print(f"  Done in {r3['duration_s']}s (rc={r3['returncode']})")
    else:
        health["runs"]["stress_concurrent"] = {
            "status": "file_not_found",
            "note": "stress/test_concurrent_access.py not found — skipped",
        }
        print("\n[3/3] stress/test_concurrent_access.py not found — skipped")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(health, indent=2), encoding="utf-8")
    print(f"\nWritten: {OUT}")
    print("Restart uvicorn to serve these numbers at GET /api/system/engineering-health")


if __name__ == "__main__":
    main()
