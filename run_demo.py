#!/usr/bin/env python
"""One-click launcher for the MEDI-COMPLY golden tests and Streamlit demo."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = Path(sys.executable).resolve()
STREAMLIT_ENTRY = ROOT / "medi_comply" / "demo_app.py"


def run_command(command: list[str], description: str, env: dict[str, str]) -> None:
    """Execute a shell command with basic logging and fail-fast semantics."""
    print(f"\n=== {description} ===")
    print("$", " ".join(command))
    result = subprocess.run(command, cwd=ROOT, env=env)
    if result.returncode != 0:
        sys.exit(result.returncode)


def ensure_streamlit_installed() -> None:
    """Guard against missing Streamlit CLI."""
    if shutil.which("streamlit") is None:
        sys.exit("Streamlit CLI not found. Install with 'pip install streamlit' and retry.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MEDI-COMPLY demo helper")
    parser.add_argument(
        "--demo-only",
        action="store_true",
        help="Skip tests and launch only the Streamlit experience",
    )
    parser.add_argument(
        "--tests-only",
        action="store_true",
        help="Run tests only and skip launching Streamlit",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.demo_only and args.tests_only:
        sys.exit("Choose either --demo-only or --tests-only, not both.")

    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(ROOT))

    print("\n🏥  MEDI-COMPLY Demo Launcher")
    print("-----------------------------")
    print("Python:", PYTHON)

    tests_cmd = [
        str(PYTHON),
        "-m",
        "pytest",
        "medi_comply/tests/test_golden_suite.py",
        "medi_comply/tests/test_end_to_end.py",
        "-v",
    ]

    if not args.demo_only:
        run_command(
            tests_cmd,
            "Running 100-case golden suite + end-to-end smoke",
            env,
        )
        print("✅ Tests complete. Total cases: 103\n")

    if not args.tests_only:
        ensure_streamlit_installed()
        if not STREAMLIT_ENTRY.exists():
            sys.exit(f"Streamlit entrypoint not found: {STREAMLIT_ENTRY}")
        run_command(
            ["streamlit", "run", str(STREAMLIT_ENTRY)],
            "Launching Streamlit demo (Ctrl+C to exit)",
            env,
        )


if __name__ == "__main__":
    main()
