"""Run the full local check suite: lint, format check, tests.

Exists as a script rather than an IDE "compound" configuration because a
compound has no process of its own, so it cannot be launched by tooling (or
report a single exit code).

    python tools/check.py           # lint + format check + tests
    python tools/check.py --fix     # reformat and autofix first
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ["src", "tests", "tools"]


def run(label: str, args: list[str]) -> bool:
    print(f"\n=== {label} ===", flush=True)
    result = subprocess.run([sys.executable, "-m", *args], cwd=ROOT)
    ok = result.returncode == 0
    print(f"--- {label}: {'ok' if ok else f'FAILED ({result.returncode})'}", flush=True)
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fix", action="store_true", help="apply formatting and lint autofixes")
    args = ap.parse_args(argv)

    steps: list[tuple[str, list[str]]] = []
    if args.fix:
        steps.append(("black (write)", ["black", *TARGETS]))
        steps.append(("ruff (fix)", ["ruff", "check", "--fix", "."]))
    else:
        steps.append(("black (check)", ["black", "--check", *TARGETS]))
        steps.append(("ruff", ["ruff", "check", "."]))
    steps.append(("pytest", ["pytest"]))

    failures = [label for label, cmd in steps if not run(label, cmd)]
    print()
    if failures:
        print(f"FAILED: {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
