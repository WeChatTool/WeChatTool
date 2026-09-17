#!/usr/bin/env python3
"""Smoke-test a built installer using only disposable, synthetic WeChat bundles.

The driver needs Python 3.10+ and macOS command line tools to build its fixtures.
The bundled backend runs from an unrelated directory with a fresh HOME and only
system directories on PATH. No installed WeChat app, account, or data is used.
Synthetic executables run directly; this script never invokes LaunchServices.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import stat
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
INSTALL_NAME = "@executable_path/../Resources/WeChatTool/WeChatTool.dylib"
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


def run(*arguments: str | Path, expected: int = 0, **kwargs: object) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(argument) for argument in arguments], capture_output=True,
        text=True, timeout=120, **kwargs,
    )
    if result.returncode != expected:
        raise AssertionError(
            f"{arguments[0]} exited {result.returncode}; expected {expected}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def require(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def snapshot(directory: Path) -> dict[str, tuple[str, int, str]]:
    """Include tree structure and modes as well as every file's content hash."""
    entries = {}
    for path in sorted(directory.rglob("*")):
        relative = str(path.relative_to(directory))
        mode = stat.S_IMODE(path.lstat().st_mode)
        if path.is_symlink():
            entries[relative] = ("symlink", mode, os.readlink(path))
        elif path.is_file():
            entries[relative] = ("file", mode, hashlib.sha256(path.read_bytes()).hexdigest())
        elif path.is_dir():
            entries[relative] = ("directory", mode, "")
        else:
            raise AssertionError(f"Unexpected fixture filesystem entry: {path}")
    return entries


def build_library(source: Path, destination: Path, arch: str) -> None:
    run("/usr/bin/xcrun", "clang", "-arch", arch, "-mmacosx-version-min=11.0",
        "-dynamiclib", source, "-o", destination)
    run("/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", destination)


def sign_fixture(bundle: Path, entitlements: Path) -> None:
    run("/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none",
        "--options", "runtime", "--entitlements", entitlements, bundle)
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", bundle)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installer", type=Path, help="Path to the built WeChatTool Installer.app.")
    parser.add_argument("--arch", choices=("arm64", "x86_64"), default=platform.machine(),
                        help="Fixture architecture; Intel fixtures on Apple Silicon require Rosetta.")
    args = parser.parse_args()
    if platform.system() != "Darwin" or args.arch not in {"arm64", "x86_64"}:
        raise SystemExit("Installer smoke tests require macOS arm64 or x86_64.")
    installer = args.installer.expanduser().resolve(strict=True)
    resources = installer / "Contents/Resources"
    backend = resources / "backend/wechattool-backend"
    plugin = resources / "WeChatTool.dylib"
    require(backend.is_file() and os.access(backend, os.X_OK), f"Missing executable backend: {backend}")
    require(plugin.is_file(), f"Missing bundled plugin: {plugin}")
    checks = 0

    def passed(name: str) -> None:
        nonlocal checks
        checks += 1
        print(f"PASS {name}", flush=True)

    with tempfile.TemporaryDirectory(prefix="wechattool-installer-smoke-") as temporary:
        # macOS commonly returns /var/... while publication resolves /private/var/....
        workspace = Path(temporary).resolve()
        unrelated = workspace / "unrelated working directory"
        home = workspace / "isolated home"
        runtime = workspace / "runtime"
        for directory in (unrelated, home, runtime):
            directory.mkdir()
        environment = {
            "PATH": SYSTEM_PATH, "HOME": str(home), "TMPDIR": str(runtime) + "/",
            "LANG": "C", "LC_ALL": "C", "USER": "fixture", "LOGNAME": "fixture",
        }

        def standalone(*arguments: str | Path, expected: int = 0) -> subprocess.CompletedProcess[str]:
            return run("/usr/bin/arch", "-arch", args.arch, backend, *arguments,
                       expected=expected, cwd=unrelated, env=environment)

        source = workspace / "Synthetic WeChat.app"
        contents = source / "Contents"
        (contents / "Resources").mkdir(parents=True)
        (contents / "MacOS").mkdir()
        launcher = contents / "MacOS/WeChat"
        library = contents / "Resources/wechat.dylib"
        metadata = {
            "CFBundleExecutable": "WeChat", "CFBundleIdentifier": "com.tencent.xinWeChat",
            "CFBundleVersion": "test-build", "CFBundleShortVersionString": "test-version",
            "CFBundlePackageType": "APPL",
        }
        (contents / "Info.plist").write_bytes(plistlib.dumps(metadata))
        build_library(ROOT / "tests/native_fixture.S", library, args.arch)
        run("/usr/bin/xcrun", "clang", "-arch", args.arch, "-mmacosx-version-min=11.0",
            "-fobjc-arc", "-Wl,-headerpad,0x100", "-framework", "Foundation",
            ROOT / "tests/native_host.m", "-o", launcher)
        entitlements = workspace / "fixture-entitlements.plist"
        entitlements.write_bytes(plistlib.dumps({
            "com.apple.security.cs.disable-library-validation": True,
        }))
        sign_fixture(source, entitlements)
        run(launcher, "baseline", "1", cwd=unrelated, env=environment)
        original = snapshot(source)
        passed("disposable original predicate returns true")

        report = json.loads(standalone("analyze", "--app", source, "--json").stdout)
        require(report["status"] == "structurally-compatible", f"Unexpected analysis: {report}")
        require(report["architectures"] == [args.arch], f"Unexpected architectures: {report}")
        require(len(report["hooks"]) == 1, f"Expected one synthetic predicate: {report}")
        require(report["hooks"][0]["image"] == "Contents/Resources/wechat.dylib", "Wrong core image")
        require(snapshot(source) == original, "Read-only analysis modified the source app")
        passed("frozen backend analyzes from unrelated cwd with sanitized environment")

        prepared = workspace / "Prepared Synthetic WeChat.app"
        result = json.loads(standalone(
            "prepare", "--app", source, "--output", prepared, "--plugin", plugin,
        ).stdout)
        require(result["status"] == "prepared-and-signature-verified", f"Unexpected prepare result: {result}")
        require(Path(result["destination"]) == prepared, "Backend published the wrong destination")
        installed_plugin = prepared / "Contents/Resources/WeChatTool/WeChatTool.dylib"
        require(installed_plugin.is_file(), "Plugin was not installed in the copied fixture")
        plan = json.loads((installed_plugin.parent / "plan.json").read_text(encoding="utf-8"))
        require(plan == report, "Installed plan differs from independently analyzed plan")
        run("/usr/bin/codesign", "--verify", "--deep", "--strict", prepared)
        run("/usr/bin/codesign", "--verify", "--strict", installed_plugin)
        require(snapshot(source) == original, "Preparation modified the source app")
        passed("standalone preparation produces a signed copy and preserves original hashes")

        prepared_launcher = prepared / "Contents/MacOS/WeChat"
        dependencies = run("/usr/bin/xcrun", "otool", "-L", prepared_launcher).stdout
        require(any(line.strip().startswith(INSTALL_NAME + " ") for line in dependencies.splitlines()),
                f"Prepared launcher is missing its plugin load command:\n{dependencies}")
        run(prepared_launcher, "normal", "0", cwd=unrelated, env=environment)
        passed("prepared synthetic host loads the plugin and disables the predicate")

        prepared_snapshot = snapshot(prepared)
        refusal = standalone("prepare", "--app", source, "--output", prepared, "--plugin", plugin, expected=1)
        require("Destination already exists" in refusal.stderr, f"Wrong refusal: {refusal.stderr}")
        require(snapshot(prepared) == prepared_snapshot, "Existing destination changed after refusal")
        require(snapshot(source) == original, "Source changed after existing-output refusal")
        passed("existing output is refused and remains unchanged")

        unsupported = workspace / "Unsupported Synthetic WeChat.app"
        shutil.copytree(source, unsupported)
        assembly = (ROOT / "tests/native_fixture.S").read_text(encoding="utf-8")
        before, after = (
            ("0x49,0xe2,0x84,0x52", "0x69,0xe2,0x84,0x52") if args.arch == "arm64" else
            ("0x81,0x7f,0x0c,0x12", "0x81,0x7f,0x0c,0x13")
        )
        require(assembly.count(before) == 1, "Synthetic predicate fixture changed; update unsupported variant")
        unsupported_assembly = workspace / "unsupported_fixture.S"
        unsupported_assembly.write_text(assembly.replace(before, after), encoding="utf-8")
        build_library(unsupported_assembly, unsupported / "Contents/Resources/wechat.dylib", args.arch)
        sign_fixture(unsupported, entitlements)
        unsupported_snapshot = snapshot(unsupported)
        unsupported_report = json.loads(standalone("analyze", "--app", unsupported, "--json", expected=2).stdout)
        require(unsupported_report["status"] == "unsupported" and not unsupported_report["hooks"],
                f"Unknown predicate was accepted: {unsupported_report}")
        refused_output = workspace / "Must Not Exist.app"
        refusal = standalone("prepare", "--app", unsupported, "--output", refused_output,
                             "--plugin", plugin, expected=1)
        require("No safe plan" in refusal.stderr, f"Wrong unsupported-app refusal: {refusal.stderr}")
        require(not refused_output.exists() and not refused_output.is_symlink(), "Unsupported app produced output")
        require(snapshot(unsupported) == unsupported_snapshot, "Unsupported app changed after refusal")
        require(snapshot(source) == original, "Original fixture hashes changed")
        passed("unsupported predicate is refused without changing source or creating output")

    print(f"{checks} installer smoke checks passed ({args.arch}; standalone frozen backend).")


if __name__ == "__main__":
    main()
