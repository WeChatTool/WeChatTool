#!/usr/bin/env python3
"""Build and run the Foundation-only recall parser tests using synthetic XML."""

from __future__ import annotations

import argparse
from pathlib import Path
import platform
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=("arm64", "x86_64"), default=platform.machine())
    args = parser.parse_args()
    if platform.system() != "Darwin":
        parser.error("These tests require macOS and Xcode Command Line Tools.")
    with tempfile.TemporaryDirectory(prefix="wechattool-recall-parser-") as temporary:
        executable = Path(temporary) / "recall-notice-tests"
        subprocess.run([
            "/usr/bin/xcrun", "clang++", "-arch", args.arch, "-mmacosx-version-min=11.0",
            "-std=c++17", "-fobjc-arc", "-Wall", "-Wextra", "-Werror", "-O2",
            "-framework", "Foundation", "-I", str(ROOT / "native"),
            str(ROOT / "native/RecallNotice.mm"), str(ROOT / "tests/recall_notice_tests.mm"),
            "-o", str(executable),
        ], check=True)
        subprocess.run(["/usr/bin/arch", "-arch", args.arch, str(executable)], check=True, timeout=30)


if __name__ == "__main__":
    main()
