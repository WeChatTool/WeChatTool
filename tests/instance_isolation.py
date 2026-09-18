#!/usr/bin/env python3
"""Prepare and exercise two isolated sandbox fixtures, never the real WeChat.

Requires an already-built plugin. Original-ID source fixtures are never run.
Only generated containers' fixture markers/preferences are touched; OS-created
container metadata remains. Intel runs on Apple Silicon require Rosetta.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import selectors
import shutil
import subprocess
import sys
import tempfile
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wechattool.prepare import prepare


def run(*args: str | Path, **kwargs: object) -> subprocess.CompletedProcess[str]:
    result = subprocess.run([str(arg) for arg in args], text=True, capture_output=True, timeout=30, **kwargs)
    if result.returncode:
        raise AssertionError(f"{args[0]} exited {result.returncode}:\n{result.stdout}\n{result.stderr}")
    return result


def snapshot(path: Path) -> dict[str, str]:
    return {str(item.relative_to(path)): hashlib.sha256(item.read_bytes()).hexdigest()
            for item in path.rglob("*") if item.is_file()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=("arm64", "x86_64"), default=platform.machine())
    parser.add_argument("--plugin", type=Path, default=ROOT / "build/WeChatTool.dylib")
    args = parser.parse_args()
    if platform.system() != "Darwin":
        raise SystemExit("Instance isolation tests require macOS.")
    plugin = args.plugin.resolve(strict=True)
    checks = 0

    def passed(name: str) -> None:
        nonlocal checks
        checks += 1
        print(f"PASS {name}", flush=True)

    with tempfile.TemporaryDirectory(prefix="wechattool-isolation-") as temporary:
        workspace = Path(temporary).resolve()
        source = workspace / "Original Fixture.app"
        contents = source / "Contents"
        (contents / "Resources").mkdir(parents=True)
        (contents / "MacOS").mkdir()
        info = {"CFBundleExecutable": "WeChat", "CFBundleIdentifier": "com.tencent.xinWeChat",
                "CFBundleVersion": "test-build", "CFBundleShortVersionString": "test-version",
                "CFBundlePackageType": "APPL", "TeamIdentifier": "5A4RE8SF68."}
        (contents / "Info.plist").write_bytes(plistlib.dumps(info))
        compile_args = ("/usr/bin/xcrun", "--sdk", "macosx", "clang", "-arch", args.arch,
                        "-mmacosx-version-min=11.0")
        run(*compile_args, "-dynamiclib", ROOT / "tests/native_fixture.S", "-o", contents / "Resources/wechat.dylib")
        run(*compile_args, "-fobjc-arc", "-framework", "Foundation", "-Wl,-headerpad,0x200",
            ROOT / "tests/instance_host.m", "-o", contents / "MacOS/WeChat")
        helper = contents / "Helpers/FixtureHelper.app"
        (helper / "Contents/MacOS").mkdir(parents=True)
        helper_info = {"CFBundleExecutable": "FixtureHelper", "CFBundleIdentifier": "local.wechattool.fixture.shared-helper",
                       "CFBundlePackageType": "APPL", "CFBundleVersion": "1"}
        (helper / "Contents/Info.plist").write_bytes(plistlib.dumps(helper_info))
        run(*compile_args, "-fobjc-arc", "-framework", "Foundation", "-DWCT_INSTANCE_HELPER=1",
            ROOT / "tests/instance_host.m", "-o", helper / "Contents/MacOS/FixtureHelper")
        helper_ent = workspace / "helper-entitlements.plist"
        helper_ent.write_bytes(plistlib.dumps({"com.apple.security.app-sandbox": True,
                                              "com.apple.security.inherit": True}))
        source_ent = workspace / "source-entitlements.plist"
        source_ent.write_bytes(plistlib.dumps({
            "com.apple.security.app-sandbox": True,
            "com.apple.application-identifier": "5A4RE8SF68.com.tencent.xinWeChat",
            "com.apple.developer.team-identifier": "5A4RE8SF68",
            "com.apple.security.application-groups": ["5A4RE8SF68.com.tencent.xinWeChat"],
        }))
        run("/usr/bin/codesign", "--force", "--sign", "-", contents / "Resources/wechat.dylib")
        run("/usr/bin/codesign", "--force", "--sign", "-", "--options", "runtime", "--entitlements", helper_ent, helper)
        run("/usr/bin/codesign", "--force", "--sign", "-", "--options", "runtime", "--entitlements", source_ent, source)
        second_source = workspace / "Second Original Fixture.app"
        shutil.copytree(source, second_source)
        source_snapshots = [(item, snapshot(item)) for item in (source, second_source)]

        copies = [workspace / "First Isolated Fixture.app", workspace / "Second Isolated Fixture.app"]
        # Synthetic images are intentionally absent from production profiles.
        # Isolate storage preparation from the separately tested selector.
        with patch("wechattool.analyze.notice_adapter", return_value="synthetic-isolation-handler"):
            reports = [prepare(original, destination, plugin)
                       for original, destination in zip((source, second_source), copies)]
        identities = []
        for app, report in zip(copies, reports):
            metadata = plistlib.loads((app / "Contents/Info.plist").read_bytes())
            plan = json.loads((app / "Contents/Resources/WeChatTool/plan.json").read_text())
            instance = metadata["WeChatToolInstanceID"]
            identifier = "local.wechattool.wechat." + instance
            assert metadata["CFBundleIdentifier"] == identifier
            assert report["bundle_id"] == plan["bundle_id"] == identifier
            assert report["instance_id"] == plan["instance_id"] == instance
            assert report["data_isolation"] == plan["data_isolation"] == "per-installation"
            assert plan["source_bundle_id"] == metadata["WeChatToolSourceBundleIdentifier"] == "com.tencent.xinWeChat"
            assert plistlib.loads((app / "Contents/Helpers/FixtureHelper.app/Contents/Info.plist").read_bytes()) == helper_info
            run("/usr/bin/codesign", "--verify", "--deep", "--strict", app)
            identities.append(identifier)
        assert identities[0] != identities[1]
        assert all(snapshot(item) == before for item, before in source_snapshots)
        passed("real preparation creates two unique signed identities without changing either source")

        unrelated = workspace / "unrelated"
        unrelated.mkdir()
        env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(unrelated),
               "TMPDIR": str(unrelated) + "/", "LANG": "C", "LC_ALL": "C"}
        markers = [uuid.uuid4().hex, uuid.uuid4().hex]

        def command(index: int, action: str) -> list[str]:
            return ["/usr/bin/arch", "-arch", args.arch, str(copies[index] / "Contents/MacOS/WeChat"), action, markers[index]]

        def probe(index: int, action: str, *, disabled: bool = False) -> dict:
            process = run(*command(index, action), cwd=unrelated,
                          env={**env, **({"WECHATTOOL_DISABLE": "1"} if disabled else {})})
            return json.loads(process.stdout)

        touched: set[int] = set()
        holders: list[subprocess.Popen[str]] = []
        try:
            results = []
            for index in range(2):
                touched.add(index)
                results.append(probe(index, "write"))
            assert results[0]["home"] != results[1]["home"]
            assert results[0]["support"] != results[1]["support"]
            assert results[0]["group"] != results[1]["group"]
            assert all(result["predicate"] == 1 for result in results)
            passed("both copies retain native classification with independent writable private and group containers")
            for index in range(2):
                result = probe(index, "read")
                assert result["helper"]["home"] == result["home"]
                assert result["helper"]["parent_value"] == result["helper"]["previous"] == markers[index]
            passed("main and unchanged inherited helpers retain isolated preferences across relaunch")
            disabled = probe(0, "disabled", disabled=True)
            assert disabled["predicate"] == 1 and disabled["home"] == results[0]["home"]
            assert disabled["group"] == results[0]["group"]
            passed("plugin disable leaves storage isolation and persisted markers intact")

            for index in range(2):
                process = subprocess.Popen(command(index, "hold"), cwd=unrelated, env=env, text=True,
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                holders.append(process)
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    assert selector.select(10), "Fixture did not report its lock within 10 seconds"
                line = process.stdout.readline()
                assert line and json.loads(line)["locked"], line or process.stderr.read()
            assert not probe(0, "try-lock")["locked"]
            assert not probe(1, "try-lock")["locked"]
            passed("two copies run concurrently while duplicate launches of each copy retain exclusive locks")
        finally:
            for process in holders:
                if process.poll() is None:
                    process.stdin.write("release\n")
                    process.stdin.flush()
                stdout, stderr = process.communicate(timeout=10)
                assert process.returncode == 0, stdout + stderr
            for index in sorted(touched):
                assert probe(index, "cleanup")["cleaned"]
        assert all(snapshot(item) == before for item, before in source_snapshots)
        passed("own markers and preferences cleaned; original fixture hashes unchanged")
    print(f"{checks} instance isolation checks passed ({args.arch}; real sandbox, inherited helper).")


if __name__ == "__main__":
    main()
