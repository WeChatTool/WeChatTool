#!/usr/bin/env python3
"""Exercise native patches in disposable, hardened synthetic app bundles.

Uses only the Python standard library and Xcode command line tools. Never opens
or changes the installed WeChat app, its data, or any account.
"""
from __future__ import annotations

import copy
import argparse
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wechattool.macho import inject_dylib
from wechattool.prepare import INSTALL_NAME


def run(*args: str | Path, **kwargs: object) -> subprocess.CompletedProcess:
    return subprocess.run([str(arg) for arg in args], check=True, capture_output=True, text=True, **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=("arm64", "x86_64"), default=platform.machine(),
                        help="Fixture architecture; running Intel fixtures on Apple Silicon requires Rosetta.")
    arch = parser.parse_args().arch
    if arch not in {"arm64", "x86_64"} or platform.system() != "Darwin":
        raise SystemExit("Native runtime tests require macOS arm64 or x86_64.")
    run(ROOT / "scripts/build-plugin.sh")
    with tempfile.TemporaryDirectory(prefix="wechattool-native-") as temporary:
        task_dir = Path(temporary)
        bundle = task_dir / "Fixture.app"
        contents = bundle / "Contents"
        resources = contents / "Resources"
        tool_dir = resources / "WeChatTool"
        tool_dir.mkdir(parents=True)
        (contents / "MacOS").mkdir()
        executable = contents / "MacOS/WeChat"
        library = resources / "wechat.dylib"
        plugin = tool_dir / "WeChatTool.dylib"
        shutil.copy2(ROOT / "build/WeChatTool.dylib", plugin)
        shutil.copy2(plugin, tool_dir / "Second.dylib")
        metadata = {
            "CFBundleExecutable": "WeChat", "CFBundleIdentifier": "com.tencent.xinWeChat",
            "CFBundleVersion": "test-build", "CFBundleShortVersionString": "test-version",
            "CFBundlePackageType": "APPL",
        }
        (contents / "Info.plist").write_bytes(plistlib.dumps(metadata))
        run("xcrun", "clang", "-arch", arch, "-mmacosx-version-min=11.0", "-dynamiclib",
            ROOT / "tests/native_fixture.S", "-o", library)
        run("xcrun", "clang", "-arch", arch, "-mmacosx-version-min=11.0", "-fobjc-arc",
            "-Wl,-headerpad,0x100", "-framework", "Foundation",
            ROOT / "tests/native_host.m", "-o", executable)
        run("codesign", "--force", "--sign", "-", library)
        symbols = run("xcrun", "nm", "-gU", library).stdout
        match = re.search(r"^([0-9a-fA-F]+)\s+\w\s+_is_revoke$", symbols, re.MULTILINE)
        assert match, symbols
        address = int(match.group(1), 16)
        uuid_output = run("xcrun", "dwarfdump", "--uuid", library).stdout
        uuid = re.search(r"UUID: ([0-9A-Fa-f-]{36})", uuid_output).group(1).lower()
        expected = "080c40b949e284521f01096be0179f1ac0035fd6" if arch == "arm64" else "554889e5817f0c122700000f94c05dc3"
        plan = {
            "schema_version": 1, "bundle_id": metadata["CFBundleIdentifier"],
            "version": metadata["CFBundleShortVersionString"], "build": metadata["CFBundleVersion"],
            "hooks": [{"arch": arch, "image": "Contents/Resources/wechat.dylib", "uuid": uuid,
                       "address": address, "expected": expected, "patch_offset": 12 if arch == "arm64" else 11,
                       "replacement": "00008052" if arch == "arm64" else "31c090", "id": "synthetic-test"}],
        }
        entitlements = task_dir / "entitlements.plist"
        entitlements.write_bytes(plistlib.dumps({
            "com.apple.security.cs.disable-library-validation": True,
            "com.apple.security.cs.allow-unsigned-executable-memory": True,
        }))
        count = 0

        def check(name: str, value: dict, wanted: int = 1, mode: str = "normal", env: dict | None = None,
                  strict_signature: bool = True, bundle_metadata: dict | None = None) -> None:
            nonlocal count
            (contents / "Info.plist").write_bytes(plistlib.dumps(
                metadata if bundle_metadata is None else bundle_metadata))
            (tool_dir / "plan.json").write_text(json.dumps(value), encoding="utf-8")
            run("codesign", "--force", "--sign", "-", "--options", "runtime", "--entitlements", entitlements, bundle)
            if strict_signature:
                run("codesign", "--verify", "--strict", bundle)
            try:
                run(executable, mode, str(wanted), env=env)
            except subprocess.CalledProcessError as error:
                raise AssertionError(f"{name}: status {error.returncode}: {error.stdout}{error.stderr}") from error
            print(f"PASS {name}", flush=True)
            count += 1

        check("unmodified predicate", plan, mode="baseline")
        check("legacy predicate-only plan safely refused", plan, wanted=1)
        original_launcher = executable.read_bytes()
        try:
            executable.write_bytes(inject_dylib(original_launcher, INSTALL_NAME))
            check("startup constructor refuses unsafe predicate-only plan", plan, wanted=1)
        finally:
            executable.write_bytes(original_launcher)
        check("already loaded image refused", plan, mode="preloaded")
        check("second plugin load leaves classifier intact", plan, wanted=1, mode="twice")
        check("environment disable", plan, env={**os.environ, "WECHATTOOL_DISABLE": "1"})
        disabled = tool_dir / "disabled"
        disabled.touch()
        check("file disable", plan)
        disabled.unlink()
        changed = copy.deepcopy(plan)
        changed["enabled"] = False
        check("plan disable", changed)
        for field, value in [("version", "other"), ("build", "other"), ("bundle_id", "other"), ("schema_version", True)]:
            changed = copy.deepcopy(plan)
            changed[field] = value
            check(f"reject {field}", changed)
        for field, value in [
            ("uuid", "00000000-0000-0000-0000-000000000000"),
            ("address", address + (4 if arch == "arm64" else 1)),
            ("address", 1 << 60),
            ("patch_offset", 0),
            ("replacement", "00000000"),
            ("expected", "00" * (len(expected) // 2)),
            ("image", "Contents/Resources/../Resources/wechat.dylib"),
            ("image", str(library)),
        ]:
            changed = copy.deepcopy(plan)
            changed["hooks"][0][field] = value
            check(f"reject hook {field}={value}", changed)
        changed = copy.deepcopy(plan)
        changed["hooks"].append(copy.deepcopy(changed["hooks"][0]))
        check("reject duplicate hook", changed)

        instance = "0123456789abcdef0123456789abcdef"
        isolated_metadata = {**metadata,
            "CFBundleIdentifier": "local.wechattool.wechat." + instance,
            "WeChatToolInstanceID": instance,
            "WeChatToolSourceBundleIdentifier": "com.tencent.xinWeChat",
        }
        isolated_plan = {**copy.deepcopy(plan),
            "bundle_id": isolated_metadata["CFBundleIdentifier"],
            "instance_id": instance, "source_bundle_id": "com.tencent.xinWeChat",
            "data_isolation": "per-installation",
        }
        check("isolated installation refuses unsafe predicate-only hook", isolated_plan, wanted=1,
              bundle_metadata=isolated_metadata)
        check("isolated copy ignores official share channels", isolated_plan, wanted=1,
              mode="sharing-filter", bundle_metadata=isolated_metadata)
        check("share isolation remains when recall protection disabled", isolated_plan,
              mode="sharing-filter", bundle_metadata=isolated_metadata,
              env={**os.environ, "WECHATTOOL_DISABLE": "1"})
        check("isolated plugin environment disable", isolated_plan, bundle_metadata=isolated_metadata,
              env={**os.environ, "WECHATTOOL_DISABLE": "1"})
        check("isolated installation refuses legacy plan", plan, bundle_metadata=isolated_metadata)
        for field, bad in [
            ("instance_id", "f" * 32), ("instance_id", True),
            ("source_bundle_id", "other"), ("source_bundle_id", ["com.tencent.xinWeChat"]),
            ("data_isolation", "shared"), ("data_isolation", True),
        ]:
            changed = copy.deepcopy(isolated_plan)
            changed[field] = bad
            check(f"isolated plan rejects {field}={bad}", changed, bundle_metadata=isolated_metadata)
        for field in ("instance_id", "source_bundle_id", "data_isolation"):
            changed = copy.deepcopy(isolated_plan)
            del changed[field]
            check(f"isolated plan requires {field}", changed, bundle_metadata=isolated_metadata)
        for field, bad in [
            ("WeChatToolInstanceID", "f" * 32), ("WeChatToolInstanceID", True),
            ("WeChatToolSourceBundleIdentifier", "other"),
            ("WeChatToolSourceBundleIdentifier", ["com.tencent.xinWeChat"]),
        ]:
            changed_metadata = {**isolated_metadata, field: bad}
            check(f"isolated bundle rejects {field}={bad}", isolated_plan, bundle_metadata=changed_metadata)
        for field in ("WeChatToolInstanceID", "WeChatToolSourceBundleIdentifier"):
            changed_metadata = dict(isolated_metadata)
            del changed_metadata[field]
            check(f"isolated bundle requires {field}", isolated_plan, bundle_metadata=changed_metadata)
        for malformed in (instance.upper(), instance[:-1], instance + "0", "g" * 32):
            # Keep metadata and plan mutually consistent: rejection must come
            # from the generated identity's shape, not an accidental mismatch.
            identifier = "local.wechattool.wechat." + malformed
            changed_metadata = {**isolated_metadata, "CFBundleIdentifier": identifier,
                                "WeChatToolInstanceID": malformed}
            changed = {**isolated_plan, "bundle_id": identifier, "instance_id": malformed}
            check(f"isolated bundle rejects malformed suffix {malformed}", changed,
                  bundle_metadata=changed_metadata)
        unknown_identifier = "local.unrelated.wechat." + instance
        check("reject unrelated bundle with matching plan", {**isolated_plan, "bundle_id": unknown_identifier},
              bundle_metadata={**isolated_metadata, "CFBundleIdentifier": unknown_identifier})
        check("legacy identity remains accepted after isolated fixtures", plan, wanted=1)

        helper = executable.with_name("Helper")
        executable.rename(helper)
        executable = helper
        metadata["CFBundleExecutable"] = "Helper"
        (contents / "Info.plist").write_bytes(plistlib.dumps(metadata))
        check("reject non-main helper executable", plan)
        executable = contents / "MacOS/WeChat"
        helper.rename(executable)
        metadata["CFBundleExecutable"] = "WeChat"
        (contents / "Info.plist").write_bytes(plistlib.dumps(metadata))
        outside = task_dir / "outside.dylib"
        shutil.copy2(library, outside)
        library.unlink()
        library.symlink_to(outside)
        # Strict bundle verification also rejects this intentionally malformed
        # symlink. Launch directly to test the module's independent path guard.
        check("reject image symlink outside bundle", plan, strict_signature=False)
        print(f"{count} native runtime checks passed ({arch}; hardened runtime, ad-hoc signature).")


if __name__ == "__main__":
    main()
