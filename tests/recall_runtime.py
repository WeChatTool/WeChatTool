#!/usr/bin/env python3
"""Run synchronous recall-handler tests using fake messages and task contexts."""
from __future__ import annotations

import argparse
from pathlib import Path
import platform
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=("arm64", "x86_64", "both"), default=platform.machine(),
                        help="Fixture architecture; Intel tests on Apple Silicon require Rosetta.")
    args = parser.parse_args()
    if platform.system() != "Darwin":
        raise SystemExit("These guarded-memory tests require macOS.")
    architectures = ("arm64", "x86_64") if args.arch == "both" else (args.arch,)
    with tempfile.TemporaryDirectory(prefix="wechattool-recall-runtime-") as temporary:
        for architecture in architectures:
            executable = Path(temporary) / f"recall-runtime-{architecture}"
            subprocess.run([
                "/usr/bin/xcrun", "clang++", "-arch", architecture,
                "-mmacosx-version-min=11.0", "-std=c++17", "-fobjc-arc",
                "-Wall", "-Wextra", "-Werror", "-O2", "-framework", "Foundation",
                "-I", str(ROOT / "native"), str(ROOT / "native/RecallNotice.mm"),
                str(ROOT / "native/RecallRuntime.mm"), str(ROOT / "tests/recall_runtime.mm"),
                "-o", str(executable),
            ], check=True, timeout=120)
            subprocess.run([
                "/usr/bin/arch", "-arch", architecture, str(executable),
            ], check=True, timeout=60)
            print(f"PASS recall runtime architecture {architecture}", flush=True)


if __name__ == "__main__":
    main()
