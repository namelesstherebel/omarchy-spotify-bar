#!/usr/bin/env python3
"""Run the real unittest suite and emit one JSON evidence record."""
import json
import os
import platform
import re
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # scripts/ -> repo root


def summarize(result):
    """Count outcomes in parent test-case units, each parent exactly once.

    Subtest outcomes are normalized to their parent test case. A parent with
    several outcome kinds is counted in the highest-precedence one:
    error > failure > unexpected success > skip > expected failure.
    """
    seen, holders, counts = set(), set(), dict.fromkeys(
        ("errors", "failed", "unexpectedSuccesses", "skipped", "expectedFailures"), 0)
    entries = ((result.errors, "errors"), (result.failures, "failed"),
               (result.unexpectedSuccesses, "unexpectedSuccesses"),
               (result.skipped, "skipped"), (result.expectedFailures, "expectedFailures"))
    for items, key in entries:  # precedence order
        for item in items:
            test = item[0] if isinstance(item, tuple) else item
            parent = getattr(test, "test_case", test)  # _SubTest -> parent
            if not isinstance(parent, unittest.TestCase):
                holders.add(id(parent))  # class/module setup holder, not a test
            elif id(parent) not in seen:
                seen.add(id(parent))
                counts[key] += 1
    bad = len(seen)
    run = result.testsRun  # never invent executed tests
    return {"executed": run, "passed": run - bad,
            "failed": counts["failed"], "errors": counts["errors"],
            "skipped": counts["skipped"],
            "expectedFailures": counts["expectedFailures"],
            "unexpectedSuccesses": counts["unexpectedSuccesses"],
            "nonTestOutcomes": len(holders),
            "status": "passed" if run > 0 and bad == 0 and not holders else "failed"}


def exit_status(summary):
    return 0 if summary.get("status") == "passed" and summary.get("executed", 0) > 0 else 1


def _head():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                              capture_output=True, text=True).stdout.strip()
    except Exception:
        return "unknown"


def main():
    started = datetime.now(timezone.utc)
    sys.path.insert(0, str(ROOT))
    summary = {"status": "error"}
    try:
        suite = unittest.TestLoader().discover(str(ROOT / "tests"))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        summary = summarize(result)
    except BaseException as exc:  # discovery/runner failure can never pass
        summary["exception"] = f"{type(exc).__name__}: {exc}"
    head = _head()
    if not re.fullmatch(r"[0-9a-f]{40}", str(head)):
        summary["status"] = "error"
        summary["exception"] = f"badhead: invalid commit SHA {head!r}"
    env = os.environ.get
    record = {"commit": head, "platform": platform.platform(),
              "python": platform.python_version(),
              "command": " ".join([sys.executable] + sys.argv),
              "started_at": started.isoformat(),
              "finished_at": datetime.now(timezone.utc).isoformat(),
              "github": {"run_id": env("GITHUB_RUN_ID"), "run_attempt": env("GITHUB_RUN_ATTEMPT"),
                         "job": env("GITHUB_JOB")},
              **summary}
    text = json.dumps(record, sort_keys=True)
    print(text, flush=True)
    path = env("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("```json\n" + text + "\n```\n")
    return exit_status(summary)


if __name__ == "__main__":
    sys.exit(main())
