#!/usr/bin/env python3
"""Test real notice detours and fallback patches in disposable synthetic apps.

The host runs directly, without LaunchServices, NSApplication, real WeChat, or
chat data. A private compiled adapter recognizes only the test fixture.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
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
from wechattool.macho import MachO


def run(*arguments: str | Path, **kwargs: object) -> subprocess.CompletedProcess[str]:
    result = subprocess.run([str(argument) for argument in arguments],
                            capture_output=True, text=True, timeout=120, **kwargs)
    if result.returncode != 0:
        raise AssertionError(f"{arguments[0]} exited {result.returncode}\n{result.stdout}{result.stderr}")
    return result


def symbol_address(library: Path, name: str) -> int:
    symbols = run("/usr/bin/xcrun", "nm", "-gU", library).stdout
    match = re.search(rf"^([0-9a-fA-F]+)\s+\w\s+_{re.escape(name)}$", symbols, re.MULTILINE)
    if not match:
        raise AssertionError(f"Missing fixture symbol {name}")
    return int(match.group(1), 16)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=("arm64", "x86_64"), default=platform.machine(),
                        help="Intel tests on Apple Silicon require Rosetta.")
    args = parser.parse_args()
    if platform.system() != "Darwin":
        parser.error("Notice hook tests require macOS and Xcode Command Line Tools.")

    with tempfile.TemporaryDirectory(prefix="wechattool-notice-hook-") as temporary:
        directory = Path(temporary).resolve()
        pristine = directory / "pristine.dylib"
        plugin = directory / "WeChatTool.dylib"
        host = directory / "host"
        metadata = {
            "CFBundleExecutable": "WeChat", "CFBundleIdentifier": "com.tencent.xinWeChat",
            "CFBundleVersion": "test-build", "CFBundleShortVersionString": "test-version",
            "CFBundlePackageType": "APPL",
        }
        run("/usr/bin/xcrun", "clang++", "-arch", args.arch, "-mmacosx-version-min=11.0",
            "-std=c++17", "-fobjc-arc", "-O2", "-framework", "Foundation", "-DWCT_NOTICE_FIXTURE",
            "-dynamiclib", ROOT / "tests/native_fixture.S", ROOT / "tests/notice_hook_host.mm", "-o", pristine)
        run("/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", pristine)
        original = pristine.read_bytes()
        image = MachO(original).slices[0]
        address = symbol_address(pristine, "is_revoke")
        def function_region(name: str) -> dict:
            start = symbol_address(pristine, name)
            assert start in image.function_starts, f"Missing function boundary: {name}"
            end = min(value for value in image.function_starts if value > start)
            offset = image.vm_to_offset(start)
            body = original[offset:offset + end - start]
            assert body and len(body) <= 65536 and image.uuid
            result = {"address": start, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
            if name == "notice_handler":
                assert len(body) >= 32
                result["expected"] = body[:32].hex()
            return result

        digest = hashlib.sha256(original).hexdigest()
        adapter_id = f"test-notice-{args.arch}"
        adapter = {
            "id": adapter_id, "arch": args.arch, "version": "test-version", "build": "test-build",
            "uuid": image.uuid, "image_sha256": digest, "predicate_address": address,
            "layout": "messagewrap-libcpp-alt-0x130" if args.arch == "arm64" else "messagewrap-libcpp-default-0x130",
            "insert_notice": function_region("insert_notice"),
            "handler": function_region("notice_handler"),
            "task_slot": function_region("task_slot"),
        }
        compiler = ["/usr/bin/xcrun", "clang++", "-arch", args.arch, "-mmacosx-version-min=11.0",
                    "-std=c++17", "-fobjc-arc", "-Wall", "-Wextra", "-Werror", "-O2",
                    "-framework", "Foundation"]
        plugin_cache: dict[str, Path] = {}

        def compile_plugin(profile: dict) -> Path:
            encoded = json.dumps({"schema_version": 1, "adapters": [profile]}, separators=(",", ":"))
            if encoded in plugin_cache:
                return plugin_cache[encoded]
            destination = directory / f"WeChatTool-{len(plugin_cache)}.dylib"
            (directory / "NoticeProfiles.inc").write_text(
                "static const char kCompiledNoticeProfiles[] = " + json.dumps(encoded) + ";\n", encoding="utf-8")
            (directory / "AccessibilityProfiles.inc").write_text(
                'static const char kCompiledAccessibilityProfiles[] = "{\\"schema_version\\":1,\\"profiles\\":[]}";\n')
            run(*compiler, "-dynamiclib", "-fvisibility=hidden", "-I", directory,
                "-Wl,-install_name,@rpath/WeChatTool.dylib", ROOT / "native/WeChatTool.mm",
                ROOT / "native/RecallNotice.mm", ROOT / "native/RecallRuntime.mm", "-o", destination)
            run("/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", destination)
            plugin_cache[encoded] = destination
            return destination

        plugin = compile_plugin(adapter)
        run(*compiler, ROOT / "tests/notice_hook_host.mm", "-o", host)
        entitlements = directory / "entitlements.plist"
        entitlements.write_bytes(plistlib.dumps({
            "com.apple.security.cs.disable-library-validation": True,
            "com.apple.security.cs.allow-unsigned-executable-memory": True,
        }))
        plan = {
            "schema_version": 1, "bundle_id": metadata["CFBundleIdentifier"],
            "version": "test-version", "build": "test-build",
            "hooks": [{"arch": args.arch, "image": "Contents/Resources/wechat.dylib", "uuid": image.uuid,
                       "address": address, "id": "synthetic-notice-test", "notice_adapter": adapter_id,
                       "image_sha256": digest,
                       "expected": "080c40b949e284521f01096be0179f1ac0035fd6" if args.arch == "arm64" else "554889e5817f0c122700000f94c05dc3",
                       "patch_offset": 12 if args.arch == "arm64" else 11,
                       "replacement": "00008052" if args.arch == "arm64" else "31c090"}],
        }
        home = directory / "isolated-home"
        home.mkdir()
        environment = {"HOME": str(home), "CFFIXED_USER_HOME": str(home), "TMPDIR": str(directory) + "/",
                       "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C", "LC_ALL": "C"}
        checks = 0

        def check(name: str, value: dict, mode: str, *, extra_environment: dict | None = None,
                  compiled_profile: dict | None = None) -> None:
            nonlocal checks
            app = directory / f"Fixture-{checks}.app"
            resources = app / "Contents/Resources"
            tool = resources / "WeChatTool"
            tool.mkdir(parents=True)
            (app / "Contents/MacOS").mkdir()
            executable = app / "Contents/MacOS/WeChat"
            library = resources / "wechat.dylib"
            shutil.copy2(host, executable)
            shutil.copy2(pristine, library)
            shutil.copy2(compile_plugin(compiled_profile) if compiled_profile is not None else plugin,
                         tool / "WeChatTool.dylib")
            (app / "Contents/Info.plist").write_bytes(plistlib.dumps(metadata))
            (tool / "plan.json").write_text(json.dumps(value), encoding="utf-8")
            run("/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", "--options", "runtime",
                "--entitlements", entitlements, app)
            run("/usr/bin/codesign", "--verify", "--deep", "--strict", app)
            before_library = library.read_bytes()
            before_plugin = (tool / "WeChatTool.dylib").read_bytes()
            try:
                run("/usr/bin/arch", "-arch", args.arch, executable, mode,
                    cwd=directory, env={**environment, **(extra_environment or {})})
            except AssertionError as error:
                raise AssertionError(f"{name}: {error}") from error
            assert library.read_bytes() == before_library, "detour modified the on-disk core image"
            assert (tool / "WeChatTool.dylib").read_bytes() == before_plugin, "plugin modified its own file"
            assert pristine.read_bytes() == original, "pristine fixture changed"
            checks += 1
            print(f"PASS {name}", flush=True)

        check("reviewed separate handler emits parsed notices and retains predicate preservation", plan, "notices")
        unknown = copy.deepcopy(plan)
        unknown["hooks"][0]["notice_adapter"] = "unknown-adapter"
        check("unknown adapter keeps baseline preservation", unknown, "baseline")
        missing = copy.deepcopy(plan)
        del missing["hooks"][0]["notice_adapter"]
        check("missing adapter keeps baseline preservation", missing, "baseline")
        changed = copy.deepcopy(plan)
        changed["hooks"][0]["image_sha256"] = "0" * 64
        check("mismatched image hash keeps baseline preservation", changed, "baseline")
        check("notice environment opt-out keeps baseline preservation", plan, "baseline",
              extra_environment={"WECHATTOOL_NOTICES": "0"})
        for region in ("handler", "insert_notice", "task_slot"):
            bad_hash = copy.deepcopy(adapter)
            bad_hash[region]["sha256"] = "0" * 64
            check(f"{region} hash mismatch retains original handler and preservation", plan, "baseline", compiled_profile=bad_hash)
        bad_prefix = copy.deepcopy(adapter)
        bad_prefix["handler"]["expected"] = "00" * 32
        check("handler prefix mismatch retains original handler and preservation", plan, "baseline", compiled_profile=bad_prefix)
        malformed = copy.deepcopy(adapter)
        malformed["task_slot"]["size"] = True
        check("malformed compiled profile retains original handler and preservation", plan, "baseline", compiled_profile=malformed)
        check("whole-plugin disable leaves original predicate", plan, "original",
              extra_environment={"WECHATTOOL_DISABLE": "1"})
        print(f"{checks} notice hook integration checks passed ({args.arch}; headless hardened fixtures).")


if __name__ == "__main__":
    main()
