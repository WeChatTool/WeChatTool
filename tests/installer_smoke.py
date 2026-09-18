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
import sys
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wechattool.analyze import analyze
from wechattool.prepare import prepare
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
    parser.add_argument("installer", type=Path, help="Path to the built WeChatTool-Installer.app.")
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
            "CFBundleName": "WeChat", "CFBundleDisplayName": "WeChat",
            "CFBundleDevelopmentRegion": "en",
            "CFBundleVersion": "test-build", "CFBundleShortVersionString": "test-version",
            "CFBundlePackageType": "APPL",
            "TeamIdentifier": "5A4RE8SF68.",
        }
        (contents / "Info.plist").write_bytes(plistlib.dumps(metadata))
        localized_metadata = {
            "en": {"CFBundleName": "WeChat", "CFBundleDisplayName": "WeChat",
                   "NSCameraUsageDescription": 'Allow "WeChat" to use your camera.'},
            "zh-Hans": {"CFBundleName": "微信", "CFBundleDisplayName": "微信",
                        "NSCameraUsageDescription": "请允许“微信”使用摄像头。"},
            "zh-Hant": {"CFBundleName": "WeChat", "CFBundleDisplayName": "WeChat",
                        "NSCameraUsageDescription": "允許「WeChat」使用攝影機。"},
        }
        for language, values in localized_metadata.items():
            localization = contents / "Resources" / (language + ".lproj")
            localization.mkdir()
            # Match the actual app's UTF-16 OpenStep strings format, which
            # plistlib alone cannot read. Keep unrelated localized permissions.
            text = "/* Synthetic localized app metadata. */\n" + "".join(
                f"{json.dumps(key)} = {json.dumps(value, ensure_ascii=False)};\n"
                for key, value in values.items()
            )
            (localization / "InfoPlist.strings").write_bytes(text.encode("utf-16"))
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
        # Never execute the original-ID fixture with a sandbox: only generated
        # identities may create fresh synthetic containers during this test.
        entitlements.write_bytes(plistlib.dumps({
            "com.apple.security.app-sandbox": True,
            "com.apple.application-identifier": "5A4RE8SF68.com.tencent.xinWeChat",
            "com.apple.security.application-groups": ["5A4RE8SF68.com.tencent.xinWeChat"],
            "com.apple.security.cs.disable-library-validation": True,
        }))
        sign_fixture(source, entitlements)
        original = snapshot(source)
        passed("disposable original predicate returns true")

        report = json.loads(standalone("analyze", "--app", source, "--json", expected=2).stdout)
        require(report["status"] == "unsupported" and not report["hooks"],
                "Frozen backend must refuse an unreviewed receive handler")
        require(any("no reviewed receive-handler" in problem for problem in report["problems"]),
                "Expected receive-handler profile refusal")
        # Production frozen builds deliberately contain no synthetic profile.
        # Test staging with an explicitly mocked source-side profile selector,
        # while still exercising the actual packaged plugin and signing paths.
        with patch("wechattool.analyze.notice_adapter", return_value="synthetic-smoke-handler"):
            report = analyze(source)
        require(report["architectures"] == [args.arch], f"Unexpected architectures: {report}")
        require(report["features"] == ["recall"], "Default feature selection changed")
        require(len(report["hooks"]) == 1, f"Expected one synthetic predicate: {report}")
        require(report["hooks"][0]["image"] == "Contents/Resources/wechat.dylib", "Wrong core image")
        require(snapshot(source) == original, "Read-only analysis modified the source app")
        passed("frozen backend refuses an unreviewed handler from unrelated cwd")

        for features in (("accessibility",), ("recall", "accessibility")):
            unsupported = json.loads(standalone(
                "analyze", "--app", source, "--json", "--features", *features, expected=2).stdout)
            require(unsupported["features"] == list(features), "Frozen backend lost feature selection")
            require(unsupported["status"] == "unsupported" and not unsupported["hooks"],
                    "Unknown accessibility build must refuse every selected hook")
        rejected = workspace / "Unsupported Accessibility.app"
        standalone("prepare", "--app", source, "--output", rejected, "--plugin", plugin,
                   "--features", "accessibility", expected=1)
        require(not rejected.exists() and snapshot(source) == original,
                "Unsupported feature preparation wrote an app or changed the source")
        passed("frozen backend preserves choices and refuses unsupported accessibility before writes")

        prepared = workspace / "Prepared Synthetic WeChat.app"
        refusal = standalone("prepare", "--app", source, "--output", prepared, "--plugin", plugin, expected=1)
        require("No safe plan" in refusal.stderr and not prepared.exists(),
                "Frozen preparation must refuse an unreviewed handler without writing output")
        with patch("wechattool.analyze.notice_adapter", return_value="synthetic-smoke-handler"):
            result = prepare(source, prepared, plugin)
        require(result["status"] == "prepared-and-signature-verified", f"Unexpected prepare result: {result}")
        require(result["features"] == ["recall"], "Prepared result must confirm the installed features")
        require(Path(result["destination"]) == prepared, "Backend published the wrong destination")
        installed_plugin = prepared / "Contents/Resources/WeChatTool/WeChatTool.dylib"
        require(installed_plugin.is_file(), "Plugin was not installed in the copied fixture")
        plan = json.loads((installed_plugin.parent / "plan.json").read_text(encoding="utf-8"))
        require(all(plan[key] == value for key, value in report.items() if key != "bundle_id"),
                "Installed hook plan differs from independently analyzed plan")
        require(result["data_isolation"] == plan["data_isolation"] == "per-installation", "Missing storage isolation")
        require(plan["bundle_id"] == result["bundle_id"] == "local.wechattool.wechat." + result["instance_id"],
                "Copied identity does not match its plan")
        require(plan["source_bundle_id"] == report["bundle_id"], "Wrong source identity")
        copied_info = plistlib.loads((prepared / "Contents/Info.plist").read_bytes())
        require(copied_info["CFBundleIdentifier"] == result["bundle_id"], "Copy retained original bundle ID")
        signed = plistlib.loads(run("/usr/bin/codesign", "-d", "--entitlements", ":-", prepared).stdout.encode())
        require(signed["com.apple.security.app-sandbox"] is True, "Copy lost its sandbox")
        require(signed["com.apple.security.application-groups"] == [result["app_group"]], "Copy retained shared group grants")
        run("/usr/bin/codesign", "--verify", "--deep", "--strict", prepared)
        run("/usr/bin/codesign", "--verify", "--strict", installed_plugin)
        require(snapshot(source) == original, "Preparation modified the source app")
        passed("source staging with packaged plugin produces a signed isolated copy")

        require(copied_info["CFBundleName"] == copied_info["CFBundleDisplayName"] == prepared.stem,
                "Base bundle metadata does not preserve the chosen installation name")
        for language, values in localized_metadata.items():
            strings = prepared / "Contents/Resources" / (language + ".lproj") / "InfoPlist.strings"
            actual = plistlib.loads(run("/usr/bin/plutil", "-convert", "xml1", "-o", "-", strings).stdout.encode())
            expected = {**values, "CFBundleName": prepared.stem, "CFBundleDisplayName": prepared.stem}
            require(actual == expected, f"Wrong localized names or changed permissions for {language}: {actual}")

        # Query Finder's display-name API and Bundle's localized lookup in a
        # fresh process for each language. This reads metadata only; it never
        # loads the fixture app's executable or registers/launches an app.
        name_source = workspace / "localized-name-probe.m"
        name_source.write_text(r'''#import <Foundation/Foundation.h>
int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc < 2) return 2;
        NSString *path = [NSString stringWithUTF8String:argv[1]];
        NSBundle *bundle = [NSBundle bundleWithPath:path];
        if (!bundle) return 3;
        NSDictionary *result = @{
            @"displayName": [[NSFileManager defaultManager] displayNameAtPath:path],
            @"bundleName": [bundle objectForInfoDictionaryKey:@"CFBundleName"] ?: @"",
            @"bundleDisplayName": [bundle objectForInfoDictionaryKey:@"CFBundleDisplayName"] ?: @"",
            @"permission": [bundle objectForInfoDictionaryKey:@"NSCameraUsageDescription"] ?: @"",
            @"language": bundle.preferredLocalizations.firstObject ?: @"",
        };
        NSData *json = [NSJSONSerialization dataWithJSONObject:result options:0 error:NULL];
        return fwrite(json.bytes, 1, json.length, stdout) == json.length ? 0 : 4;
    }
}
''', encoding="utf-8")
        name_probe = workspace / "localized-name-probe"
        name_probe_info = workspace / "localized-name-probe.plist"
        name_probe_info.write_bytes(plistlib.dumps({
            "CFBundleIdentifier": "local.wechattool.metadata-probe",
            # A command-line main bundle otherwise constrains another bundle's
            # resource lookup to its own default language (English).
            "CFBundleAllowMixedLocalizations": True,
        }))
        run("/usr/bin/xcrun", "clang", "-arch", args.arch, "-mmacosx-version-min=11.0",
            "-fobjc-arc", "-framework", "Foundation",
            "-Xlinker", "-sectcreate", "-Xlinker", "__TEXT", "-Xlinker", "__info_plist",
            "-Xlinker", name_probe_info, name_source, "-o", name_probe)
        for language, values in localized_metadata.items():
            names = json.loads(run(name_probe, prepared, "-AppleLanguages", f"({language})",
                                   cwd=unrelated, env=environment).stdout)
            require(names["language"] == language, f"Name probe did not select {language}: {names}")
            require(all(names[key] == prepared.stem for key in ("displayName", "bundleName", "bundleDisplayName")),
                    f"macOS name lookup lost the chosen installation name for {language}: {names}")
            require(names["permission"] == values["NSCameraUsageDescription"],
                    f"Localized permission changed for {language}: {names}")
        require(snapshot(source) == original, "Localized-name checks changed the source fixture")
        passed("chosen installation name survives English and both Chinese localized macOS lookups")

        prepared_launcher = prepared / "Contents/MacOS/WeChat"
        dependencies = run("/usr/bin/xcrun", "otool", "-L", prepared_launcher).stdout
        require(any(line.strip().startswith(INSTALL_NAME + " ") for line in dependencies.splitlines()),
                f"Prepared launcher is missing its plugin load command:\n{dependencies}")
        run(prepared_launcher, "normal", "1", cwd=unrelated, env=environment)
        passed("packaged plugin refuses unreviewed handler and keeps outgoing classifier intact")

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
