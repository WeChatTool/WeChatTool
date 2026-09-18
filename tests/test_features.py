"""Feature choices must stay independent and fail closed before publishing a copy."""

import contextlib
import copy
import hashlib
import io
import json
import plistlib
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_analyze import ARM
from test_macho import VM_BASE, make_fat, make_thin
from wechattool.__main__ import main
from wechattool.accessibility import (profile_id, profiles, scan_accessibility,
                                      validate_document)
from wechattool.analyze import CompatibilityError, analyze
from wechattool.features import selected_features
from wechattool.macho import MachO
from wechattool.prepare import prepare


INTERFACE = bytes.fromhex("0000001401000014c0035fd61f2003d5")
ACTIONS = bytes.fromhex("00008052c0035fd61f2003d51f2003d5")


def fixture(*, arch="arm64", recall=True, starts=(0x400, 0x420, 0x430), uuid_bytes=bytes(range(16))):
    prefix = ARM if recall else b"\0" * len(ARM)
    return make_thin(arch=arch, text=prefix + b"\0" * (32 - len(prefix)) + INTERFACE + ACTIONS,
                     starts=starts, uuid_bytes=uuid_bytes)


def rules_for(data):
    result = []
    for part in MachO(data).slices:
        for role, offset in (("interface", 0x420), ("actions", 0x430)):
            address = VM_BASE + offset
            index = part.vm_to_offset(address, 16)
            body = data[index:index + 16]
            result.append({"id": profile_id(role, part.arch), "arch": part.arch,
                           "version": "99.0", "build": "test", "uuid": part.uuid,
                           "image_sha256": hashlib.sha256(data).hexdigest(), "address": address,
                           "expected": body.hex(), "patch_offset": 0,
                           "replacement": "1f2003d5" if part.arch == "arm64" else "90",
                           "function_size": 16, "function_sha256": hashlib.sha256(body).hexdigest()})
    return result


class SelectionTests(unittest.TestCase):
    def test_default_and_deterministic_order(self):
        self.assertEqual(selected_features(), ["recall"])
        self.assertEqual(selected_features(["accessibility", "recall"]), ["recall", "accessibility"])
        self.assertEqual(selected_features(["accessibility"]), ["accessibility"])

    def test_invalid_selections_rejected(self):
        for selection in ([], ["recall", "recall"], ["other"], "recall", [None]):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                selected_features(selection)


class AccessibilityProfileTests(unittest.TestCase):
    def test_packaged_profiles_are_valid_and_cover_both_architectures(self):
        rules = profiles()
        self.assertEqual({rule["id"] for rule in rules},
                         {profile_id(role, arch) for role in ("interface", "actions")
                          for arch in ("arm64", "x86_64")})

    def test_complete_pair_in_each_slice(self):
        data = make_fat(fixture(), fixture(arch="x86_64"))
        found, details = scan_accessibility(data, rules_for(data), "99.0", "test")
        self.assertEqual(len(found), 4)
        self.assertTrue(all(detail["status"] == "matched" for detail in details))

    def test_metadata_mismatch_refuses_pair(self):
        data = fixture()
        for key, value in (("version", "100"), ("build", "other"), ("uuid", "f" * 8 + "-" + "f" * 4 +
                           "-" + "f" * 4 + "-" + "f" * 4 + "-" + "f" * 12),
                           ("image_sha256", "0" * 64), ("function_sha256", "0" * 64)):
            rules = rules_for(data)
            rules[0][key] = value
            with self.subTest(key=key):
                found, details = scan_accessibility(data, rules, "99.0", "test")
                self.assertEqual(found, [])
                self.assertEqual(details[0]["status"], "unsupported")

    def test_complete_function_hash_covers_bytes_beyond_patch(self):
        data = bytearray(fixture())
        rules = rules_for(data)
        data[0x42F] ^= 1
        # Even an updated image hash does not relax full-function verification.
        for rule in rules:
            rule["image_sha256"] = hashlib.sha256(data).hexdigest()
        found, _ = scan_accessibility(bytes(data), rules, "99.0", "test")
        self.assertEqual(found, [])

    def test_exact_function_boundaries_and_code_permissions_required(self):
        cases = [fixture(starts=(0x400, 0x430)), fixture(starts=(0x400, 0x420, 0x428, 0x430)),
                 fixture(uuid_bytes=None)]
        read_only = bytearray(fixture())
        struct.pack_into("<I", read_only, 32 + 60, 1)
        cases.append(bytes(read_only))
        not_code = bytearray(fixture())
        struct.pack_into("<I", not_code, 32 + 72 + 64, 0x400)
        cases.append(bytes(not_code))
        for data in cases:
            rules = rules_for(fixture())
            for rule in rules:
                rule["image_sha256"] = hashlib.sha256(data).hexdigest()
            with self.subTest(digest=hashlib.sha256(data).hexdigest()):
                found, _ = scan_accessibility(data, rules, "99.0", "test")
                self.assertEqual(found, [])

    def test_ambiguous_match_refuses_both_hooks(self):
        data = fixture()
        rules = rules_for(data)
        found, details = scan_accessibility(data, rules + [rules[0]], "99.0", "test")
        self.assertEqual(found, [])
        self.assertEqual(details[0]["status"], "ambiguous")

    def test_profile_document_rejects_corrupt_and_duplicate_rules(self):
        base = {"schema_version": 1, "profiles": rules_for(fixture())}
        corrupt = []
        for key, value in (("address", True), ("address", 0), ("address", -1), ("address", (1 << 64) - 1),
                           ("patch_offset", 14), ("function_size", 65540), ("function_size", 12),
                           ("expected", "00 00"), ("expected", "00" * 2049),
                           ("replacement", "00" * 20), ("uuid", "bad"), ("id", "arbitrary"),
                           ("image_sha256", "FF" * 32), ("version", "x" * 65), ("build", "a\nb")):
            document = copy.deepcopy(base)
            document["profiles"][0][key] = value
            corrupt.append(document)
        duplicate = copy.deepcopy(base)
        duplicate["profiles"].append(duplicate["profiles"][0])
        corrupt.extend([duplicate, {"schema_version": 2, "profiles": []},
                        {"schema_version": True, "profiles": []},
                        {"schema_version": 1, "profiles": None}])
        for document in corrupt:
            with self.subTest(document=document), self.assertRaises(ValueError):
                validate_document(document)


class FeatureBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "WeChat.app"
        (self.app / "Contents/MacOS").mkdir(parents=True)
        (self.app / "Contents/Resources").mkdir()
        info = {"CFBundleIdentifier": "com.tencent.xinWeChat", "CFBundleExecutable": "WeChat",
                "CFBundleShortVersionString": "99.0", "CFBundleVersion": "test",
                "TeamIdentifier": "5A4RE8SF68."}
        (self.app / "Contents/Info.plist").write_bytes(plistlib.dumps(info))
        self.launcher = self.app / "Contents/MacOS/WeChat"
        self.launcher.write_bytes(make_thin())
        self.core = self.app / "Contents/Resources/wechat.dylib"
        self.core.write_bytes(fixture())
        self.rules = rules_for(self.core.read_bytes())

    def test_default_does_not_load_accessibility_profiles(self):
        with patch("wechattool.analyze.accessibility_profiles", side_effect=AssertionError("unselected")):
            report = analyze(self.app)
        self.assertEqual(report["features"], ["recall"])
        self.assertEqual([hook["feature"] for hook in report["hooks"]], ["recall"])

    def test_accessibility_only_does_not_need_recall_matcher(self):
        self.core.write_bytes(fixture(recall=False))
        with patch("wechattool.analyze.accessibility_profiles", return_value=rules_for(self.core.read_bytes())), \
                patch("wechattool.analyze.scan_image", side_effect=AssertionError("unselected")):
            report = analyze(self.app, features=["accessibility"])
        self.assertEqual(report["status"], "structurally-compatible")
        self.assertEqual(report["features"], ["accessibility"])
        self.assertEqual(len(report["hooks"]), 2)
        self.assertEqual(report["recall_notices"]["status"], "not-selected")
        self.assertFalse(any("notice_adapter" in hook for hook in report["hooks"]))

    def test_both_features_use_one_image_and_three_hooks(self):
        with patch("wechattool.analyze.accessibility_profiles", return_value=self.rules):
            report = analyze(self.app, features=["accessibility", "recall"])
        self.assertEqual(report["status"], "structurally-compatible")
        self.assertEqual(report["features"], ["recall", "accessibility"])
        self.assertEqual(len(report["hooks"]), 3)
        self.assertEqual(len(report["images"]), 1)

    def test_unsupported_selected_feature_refuses_all_hooks(self):
        with patch("wechattool.analyze.accessibility_profiles", return_value=self.rules[:1]):
            report = analyze(self.app, features=["recall", "accessibility"])
        self.assertEqual(report["status"], "unsupported")
        self.assertEqual(report["hooks"], [])
        self.assertIn("accessibility", " ".join(report["problems"]))

    def test_missing_launcher_architecture_refuses_all_hooks(self):
        self.launcher.write_bytes(make_fat())
        with patch("wechattool.analyze.accessibility_profiles", return_value=self.rules):
            report = analyze(self.app, features=["accessibility"])
        self.assertEqual(report["hooks"], [])
        self.assertIn("x86_64 accessibility", " ".join(report["problems"]))

    def test_duplicate_pairs_across_images_refused(self):
        (self.app / "Contents/Frameworks").mkdir()
        (self.app / "Contents/Frameworks/wechat.dylib").write_bytes(self.core.read_bytes())
        with patch("wechattool.analyze.accessibility_profiles", return_value=self.rules):
            report = analyze(self.app, features=["accessibility"])
        self.assertEqual(report["hooks"], [])
        self.assertEqual(report["status"], "unsupported")

    def test_prepare_rechecks_same_features_before_signing(self):
        plugin = self.root / "plugin.dylib"
        plugin.write_bytes(make_thin())
        destination = self.root / "Prepared.app"

        def fake_copy(*arguments):
            shutil.copytree(arguments[1], arguments[2], symlinks=True)

        with patch("wechattool.analyze.accessibility_profiles", return_value=self.rules), \
                patch("wechattool.prepare.analyze", wraps=analyze) as analyzer, \
                patch("wechattool.prepare.run", side_effect=fake_copy), \
                patch("wechattool.prepare.sign_copy", side_effect=subprocess.CalledProcessError(1, "codesign")):
            with self.assertRaises(subprocess.CalledProcessError):
                prepare(self.app, destination, plugin, features=["accessibility"])
        self.assertEqual(analyzer.call_count, 2)
        self.assertEqual([call.kwargs["features"] for call in analyzer.call_args_list],
                         [["accessibility"], ["accessibility"]])
        self.assertFalse(destination.exists())

    def test_prepared_report_and_embedded_plan_keep_selection(self):
        plugin = self.root / "plugin.dylib"
        plugin.write_bytes(make_thin())
        embedded = []

        def fake_copy(*arguments):
            shutil.copytree(arguments[1], arguments[2], symlinks=True)

        def inspect_plan(app, *arguments):
            embedded.append(json.loads((app / "Contents/Resources/WeChatTool/plan.json").read_text()))

        with patch("wechattool.analyze.accessibility_profiles", return_value=self.rules), \
                patch("wechattool.prepare.run", side_effect=fake_copy), \
                patch("wechattool.prepare.sign_copy", side_effect=inspect_plan), \
                patch("ctypes.CDLL") as library:
            library.return_value.renamex_np.return_value = 0
            report = prepare(self.app, self.root / "Prepared.app", plugin, features=["accessibility"])
        self.assertEqual(report["features"], ["accessibility"])
        self.assertEqual(embedded[0]["features"], ["accessibility"])
        self.assertEqual({hook["feature"] for hook in embedded[0]["hooks"]}, {"accessibility"})

    def test_cli_feature_arguments_and_invalid_selection(self):
        stdout = io.StringIO()
        with patch("wechattool.analyze.accessibility_profiles", return_value=self.rules), \
                contextlib.redirect_stdout(stdout):
            result = main(["analyze", "--app", str(self.app), "--features", "accessibility", "--json"])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(stdout.getvalue())["features"], ["accessibility"])
        with contextlib.redirect_stderr(io.StringIO()):
            result = main(["analyze", "--app", str(self.app), "--features", "recall", "recall"])
        self.assertEqual(result, 1)
        for selection in ([], ["unknown"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                main(["analyze", "--features", *selection])
            self.assertEqual(error.exception.code, 2)

    def test_prepare_cli_forwards_selection(self):
        with patch("wechattool.__main__.prepare", return_value={}) as prepare_copy, \
                patch("wechattool.__main__.sys.platform", "darwin"), \
                contextlib.redirect_stdout(io.StringIO()):
            result = main(["prepare", "--app", str(self.app), "--output", str(self.root / "New.app"),
                           "--features", "recall", "accessibility"])
        self.assertEqual(result, 0)
        self.assertEqual(prepare_copy.call_args.kwargs["features"], ["recall", "accessibility"])


if __name__ == "__main__":
    unittest.main()
