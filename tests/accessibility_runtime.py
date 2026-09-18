#!/usr/bin/env python3
"""Exercise optional accessibility patches in harmless hardened fixture apps.

Compiles a private profile registry bound only to the synthetic fixture. Never
loads installed WeChat, touches its settings, or accesses conversations.
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
    result = subprocess.run([str(argument) for argument in arguments], capture_output=True,
                            text=True, timeout=120, **kwargs)
    if result.returncode:
        raise AssertionError(f"{arguments[0]} exited {result.returncode}\n{result.stdout}{result.stderr}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=("arm64", "x86_64"), default=platform.machine(),
                        help="Intel execution on Apple Silicon requires Rosetta.")
    arch = parser.parse_args().arch
    if platform.system() != "Darwin":
        parser.error("Native accessibility tests require macOS and Xcode Command Line Tools.")
    with tempfile.TemporaryDirectory(prefix="wechattool-accessibility-") as temporary:
        directory = Path(temporary).resolve()
        pristine = directory / "pristine.dylib"
        host = directory / "host"
        compiler = ["/usr/bin/xcrun", "clang++", "-arch", arch, "-mmacosx-version-min=11.0",
                    "-std=c++17", "-fobjc-arc", "-O2", "-framework", "Foundation"]
        run(*compiler, "-dynamiclib", ROOT / "tests/native_fixture.S",
            ROOT / "tests/accessibility_fixture.S", "-o", pristine)
        run("/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", pristine)
        run(*compiler, ROOT / "tests/accessibility_host.mm", "-o", host)
        original = pristine.read_bytes()
        image = MachO(original).slices[0]
        image_hash = hashlib.sha256(original).hexdigest()
        symbols = run("/usr/bin/xcrun", "nm", "-gU", pristine).stdout

        def address(name: str) -> int:
            match = re.search(rf"^([0-9a-fA-F]+)\s+\w\s+_{re.escape(name)}$", symbols, re.MULTILINE)
            assert match, f"Missing fixture symbol {name}"
            return int(match.group(1), 16)

        metadata = {"CFBundleExecutable": "WeChat", "CFBundleIdentifier": "com.tencent.xinWeChat",
                    "CFBundleVersion": "test-build", "CFBundleShortVersionString": "test-version",
                    "CFBundlePackageType": "APPL"}
        profiles = []
        for role in ("interface", "actions"):
            start = address("fixture_" + role)
            assert start in image.function_starts
            end = min(value for value in image.function_starts if value > start)
            offset = image.vm_to_offset(start)
            body = original[offset:offset + end - start]
            replacement = ("1f2003d5" if arch == "arm64" else "9090") if role == "interface" else (
                "20008052c0035fd6" if arch == "arm64" else "b801000000c390")
            patch_offset = 12 if role == "interface" else 0
            if role == "interface":
                assert (body[12:16] == bytes.fromhex("68000034") if arch == "arm64" else body[12] == 0x74)
            profiles.append({
                "id": f"accessibility-{role}-{arch}-v1", "arch": arch,
                "version": "test-version", "build": "test-build", "uuid": image.uuid,
                "image_sha256": image_hash, "address": start, "expected": body.hex(),
                "patch_offset": patch_offset, "replacement": replacement,
                "function_size": len(body), "function_sha256": hashlib.sha256(body).hexdigest(),
            })
        recall = {"id": "synthetic-recall-test", "feature": "recall", "arch": arch,
                  "image": "Contents/Resources/wechat.dylib", "uuid": image.uuid,
                  "address": address("is_revoke"), "patch_offset": 12 if arch == "arm64" else 11,
                  "expected": "080c40b949e284521f01096be0179f1ac0035fd6" if arch == "arm64" else
                              "554889e5817f0c122700000f94c05dc3",
                  "replacement": "00008052" if arch == "arm64" else "31c090"}

        def plan(features: list[str], registry: list[dict] | None = None) -> dict:
            hooks = [copy.deepcopy(recall)] if "recall" in features else []
            if "accessibility" in features:
                hooks.extend({**copy.deepcopy(profile), "feature": "accessibility",
                              "image": "Contents/Resources/wechat.dylib"}
                             for profile in (profiles if registry is None else registry))
            return {"schema_version": 1, "bundle_id": metadata["CFBundleIdentifier"],
                    "version": "test-version", "build": "test-build", "features": features, "hooks": hooks}

        plugin_cache: dict[str, Path] = {}

        def compile_plugin(registry: list[dict]) -> Path:
            encoded = json.dumps({"schema_version": 1, "profiles": registry}, separators=(",", ":"))
            if encoded in plugin_cache:
                return plugin_cache[encoded]
            destination = directory / f"WeChatTool-{len(plugin_cache)}.dylib"
            (directory / "AccessibilityProfiles.inc").write_text(
                "static const char kCompiledAccessibilityProfiles[] = " + json.dumps(encoded) + ";\n")
            (directory / "NoticeProfiles.inc").write_text(
                'static const char kCompiledNoticeProfiles[] = "{\\"schema_version\\":1,\\"adapters\\":[]}";\n')
            run(*compiler, "-dynamiclib", "-fvisibility=hidden", "-I", directory,
                "-Wl,-install_name,@rpath/WeChatTool.dylib", ROOT / "native/WeChatTool.mm",
                ROOT / "native/RecallNotice.mm", ROOT / "native/RecallRuntime.mm", "-o", destination)
            run("/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", destination)
            plugin_cache[encoded] = destination
            return destination

        default_plugin = compile_plugin(profiles)
        entitlements = directory / "entitlements.plist"
        entitlements.write_bytes(plistlib.dumps({
            "com.apple.security.cs.disable-library-validation": True,
            "com.apple.security.cs.allow-unsigned-executable-memory": True,
        }))
        isolated_home = directory / "isolated-home"
        isolated_home.mkdir()
        environment = {"HOME": str(isolated_home), "CFFIXED_USER_HOME": str(isolated_home),
                       "TMPDIR": str(directory) + "/", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                       "LANG": "C", "LC_ALL": "C"}
        count = 0

        def check(name: str, value: dict, expected: tuple[int, int, int] = (1, 0, 0), *,
                  mode: str = "normal", registry: list[dict] | None = None,
                  extra_environment: dict | None = None, disabled_file: bool = False) -> None:
            nonlocal count
            app = directory / f"Fixture-{count}.app"
            tool = app / "Contents/Resources/WeChatTool"
            tool.mkdir(parents=True)
            (app / "Contents/MacOS").mkdir()
            executable = app / "Contents/MacOS/WeChat"
            library = app / "Contents/Resources/wechat.dylib"
            plugin = tool / "WeChatTool.dylib"
            shutil.copy2(host, executable)
            shutil.copy2(pristine, library)
            shutil.copy2(default_plugin if registry is None else compile_plugin(registry), plugin)
            (app / "Contents/Info.plist").write_bytes(plistlib.dumps(metadata))
            (tool / "plan.json").write_text(json.dumps(value))
            if disabled_file:
                (tool / "disabled").touch()
            run("/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", "--options", "runtime",
                "--entitlements", entitlements, app)
            run("/usr/bin/codesign", "--verify", "--deep", "--strict", app)
            before = (library.read_bytes(), plugin.read_bytes())
            try:
                run("/usr/bin/arch", "-arch", arch, executable, mode, *(str(x) for x in expected),
                    cwd=directory, env={**environment, **(extra_environment or {})})
            except AssertionError as error:
                raise AssertionError(f"{name}: {error}") from error
            assert (library.read_bytes(), plugin.read_bytes()) == before, "runtime changed an on-disk library"
            assert pristine.read_bytes() == original, "pristine fixture changed"
            count += 1
            print(f"PASS {name}", flush=True)

        both = plan(["recall", "accessibility"])
        ax = plan(["accessibility"])
        check("unmodified fixture", both, mode="baseline")
        check("unreviewed recall-only plan leaves all functions unchanged", plan(["recall"]), (1, 0, 0))
        legacy = plan(["recall"])
        del legacy["features"]
        del legacy["hooks"][0]["feature"]
        check("legacy predicate-only plan is safely refused", legacy, (1, 0, 0))
        check("accessibility-only preserves recall behavior and null guard", ax, (1, 7, 1))
        check("accessibility works without unsafe recall fallback", both, (1, 7, 1))
        reverse = copy.deepcopy(both)
        reverse["hooks"].reverse()
        check("accessibility pair preflight supports reversed hook order", reverse, (1, 7, 1))
        check("already loaded image refused", both, mode="preloaded")
        check("global environment disable", both, extra_environment={"WECHATTOOL_DISABLE": "1"})
        check("global file disable", both, disabled_file=True)
        check("global plan disable", {**both, "enabled": False})
        for field, value in [
            ("id", "unknown-profile"), ("uuid", "00000000-0000-0000-0000-000000000000"),
            ("image_sha256", "0" * 64), ("function_sha256", "0" * 64),
            ("function_size", 1), ("expected", "00" * (len(profiles[0]["expected"]) // 2)),
            ("replacement", "00" * (len(profiles[0]["replacement"]) // 2)),
            ("patch_offset", 0), ("address", profiles[0]["address"] + 1),
        ]:
            changed = copy.deepcopy(ax)
            changed["hooks"][0][field] = value
            check(f"unreviewed plan {field} refused", changed)
        for feature_value in (None, [], ["recall"], ["accessibility", "accessibility"], ["unknown"]):
            changed = copy.deepcopy(ax)
            if feature_value is None:
                del changed["features"]
            else:
                changed["features"] = feature_value
            check(f"invalid feature selection {feature_value} refused", changed)
        changed = copy.deepcopy(ax)
        del changed["hooks"][0]["feature"]
        check("accessibility hook requires feature label", changed)
        changed = copy.deepcopy(ax)
        changed["hooks"].pop()
        check("partial accessibility pair refused", changed)
        changed = copy.deepcopy(ax)
        changed["hooks"][1] = copy.deepcopy(changed["hooks"][0])
        check("duplicate accessibility hook refused", changed)
        # Matching plan/compiled registry must still pass actual image/function
        # validation. Alter the second function's hash to verify pair preflight
        # refuses the first function too, before any patch is applied.
        changed_registry = copy.deepcopy(profiles)
        changed_registry[1]["function_sha256"] = "0" * 64
        check("second function digest failure refuses the whole pair", plan(["accessibility"], changed_registry),
              registry=changed_registry)
        for field, bad in [("image_sha256", "0" * 64),
                           ("uuid", "00000000-0000-0000-0000-000000000000")]:
            changed_registry = copy.deepcopy(profiles)
            for profile in changed_registry:
                profile[field] = bad
            check(f"actual image {field} verified independently", plan(["accessibility"], changed_registry),
                  registry=changed_registry)
        print(f"{count} accessibility runtime checks passed ({arch}; harmless hardened fixtures).")


if __name__ == "__main__":
    main()
