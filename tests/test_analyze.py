import json
import plistlib
import shutil
import subprocess
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_macho import make_thin
from wechattool.analyze import CompatibilityError, analyze, confined_path, profiles, scan_image
from wechattool.prepare import isolate_helpers, prepare
from wechattool.instances import create_identity, isolated_file_provider_entitlements


ARM = bytes.fromhex("080c40b949e284521f01096be0179f1ac0035fd6")
INTEL = bytes.fromhex("554889e5817f0c122700000f94c05dc3")


class AnalyzerTests(unittest.TestCase):
    def test_architectures_and_shifted_functions(self):
        for arch, code in (("arm64", ARM), ("x86_64", INTEL)):
            for offset in (0x400, 0x600):
                with self.subTest(arch=arch, offset=offset):
                    image = make_thin(arch=arch, text=code, text_offset=offset, starts=(offset,))
                    hooks, diagnostics = scan_image(image, profiles())
                    self.assertEqual(len(hooks), 1)
                    self.assertEqual(hooks[0]["address"], 0x100000000 + offset)
                    self.assertEqual(diagnostics[0]["status"], "matched")

    def test_ambiguous_functions_refused(self):
        image = make_thin(text=ARM + b"\0" * 12 + ARM, starts=(0x400, 0x420))
        hooks, diagnostics = scan_image(image, profiles())
        self.assertEqual(hooks, [])
        self.assertEqual(diagnostics[0]["status"], "ambiguous")

    def test_missing_function_starts_refused(self):
        hooks, diagnostics = scan_image(make_thin(text=ARM, starts=None), profiles())
        self.assertEqual(hooks, [])
        self.assertEqual(diagnostics[0]["rejected_non_function_matches"], 1)

    def test_embedded_match_not_a_function_refused(self):
        hooks, _ = scan_image(make_thin(text=b"\0" * 4 + ARM, starts=(0x400,)), profiles())
        self.assertEqual(hooks, [])

    def test_modified_instruction_refused(self):
        code = bytearray(ARM)
        code[4] ^= 1
        hooks, _ = scan_image(make_thin(text=bytes(code)), profiles())
        self.assertEqual(hooks, [])

    def test_nonexecutable_segment_refused(self):
        image = bytearray(make_thin(text=ARM))
        struct.pack_into("<I", image, 32 + 60, 1)  # __TEXT initprot = read only.
        hooks, _ = scan_image(bytes(image), profiles())
        self.assertEqual(hooks, [])

    def test_non_pure_code_section_refused(self):
        image = bytearray(make_thin(text=ARM))
        struct.pack_into("<I", image, 32 + 72 + 64, 0x400)  # SOME, but not PURE instructions.
        hooks, _ = scan_image(bytes(image), profiles())
        self.assertEqual(hooks, [])


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "WeChat.app"
        (self.app / "Contents/MacOS").mkdir(parents=True)
        (self.app / "Contents/Resources").mkdir()
        self.info = {
            "CFBundleIdentifier": "com.tencent.xinWeChat", "CFBundleExecutable": "WeChat",
            "CFBundleShortVersionString": "99.0", "CFBundleVersion": "future-build",
            "TeamIdentifier": "5A4RE8SF68.",
        }
        (self.app / "Contents/Info.plist").write_bytes(plistlib.dumps(self.info))
        (self.app / "Contents/MacOS/WeChat").write_bytes(make_thin(text=b"\xc0\x03\x5f\xd6"))
        (self.app / "Contents/Resources/wechat.dylib").write_bytes(make_thin(text=ARM))

    def test_future_build_uses_capability_not_version_number(self):
        report = analyze(self.app)
        self.assertEqual(report["status"], "structurally-compatible")
        self.assertIn("static-only", report["validation"])
        self.assertEqual(len(report["hooks"]), 1)
        self.assertEqual(report["hooks"][0]["image"], "Contents/Resources/wechat.dylib")
        self.assertEqual(len(report["images"][0]["sha256"]), 64)
        # Plan has no absolute user path or message content.
        self.assertNotIn(str(self.root), json.dumps(report))

    def test_duplicate_across_images_refused(self):
        (self.app / "Contents/Frameworks").mkdir()
        (self.app / "Contents/Frameworks/wechat.dylib").write_bytes(make_thin(text=ARM))
        report = analyze(self.app)
        self.assertEqual(report["status"], "unsupported")
        self.assertEqual(report["hooks"], [])

    def test_explicit_future_image_location(self):
        core = self.app / "Contents/Resources/wechat.dylib"
        core.rename(core.with_name("future-core.dylib"))
        report = analyze(self.app, "Contents/Resources/future-core.dylib")
        self.assertEqual(report["status"], "structurally-compatible")

    def test_main_executable_hook_refused(self):
        with self.assertRaises(CompatibilityError):
            analyze(self.app, "Contents/MacOS/WeChat")

    def test_invalid_plist_type_refused(self):
        (self.app / "Contents/Info.plist").write_bytes(plistlib.dumps([]))
        with self.assertRaises(CompatibilityError):
            analyze(self.app)

    def test_symlink_escape_refused(self):
        outside = self.root / "outside.dylib"
        outside.write_bytes(make_thin(text=ARM))
        core = self.app / "Contents/Resources/wechat.dylib"
        core.unlink()
        core.symlink_to(outside)
        with self.assertRaises(CompatibilityError):
            analyze(self.app)

    def test_traversal_refused(self):
        for path in ("../outside", "/tmp/outside", "Contents/../../outside"):
            with self.subTest(path=path), self.assertRaises(CompatibilityError):
                confined_path(self.app, path)

    def test_already_prepared_refused(self):
        directory = self.app / "Contents/Resources/WeChatTool"
        directory.mkdir()
        (directory / "plan.json").write_text("{}")
        with self.assertRaises(CompatibilityError):
            analyze(self.app)

    def test_prepare_refuses_existing_destination_before_writes(self):
        before = (self.app / "Contents/MacOS/WeChat").read_bytes()
        with self.assertRaises(CompatibilityError):
            prepare(self.app, self.app, self.root / "missing-plugin.dylib")
        self.assertEqual(before, (self.app / "Contents/MacOS/WeChat").read_bytes())

    def test_prepare_refuses_nested_destination_before_writes(self):
        destination = self.app / "Nested.app"
        with self.assertRaises(CompatibilityError):
            prepare(self.app, destination, self.root / "missing-plugin.dylib")
        self.assertFalse(destination.exists())

    def test_signing_failure_does_not_publish_or_change_source(self):
        destination = self.root / "Prepared.app"
        plugin = self.root / "plugin.dylib"
        plugin.write_bytes(make_thin(text=b"\xc0\x03\x5f\xd6"))
        before = (self.app / "Contents/MacOS/WeChat").read_bytes()

        def fake_copy(*arguments):
            self.assertEqual(arguments[0], "/usr/bin/ditto")
            shutil.copytree(arguments[1], arguments[2], symlinks=True)

        with patch("wechattool.prepare.run", side_effect=fake_copy), patch(
            "wechattool.prepare.sign_copy", side_effect=subprocess.CalledProcessError(1, "codesign")
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                prepare(self.app, destination, plugin)
        self.assertFalse(destination.exists())
        self.assertEqual(list(self.root.glob(".wechattool-*")), [])
        self.assertEqual(before, (self.app / "Contents/MacOS/WeChat").read_bytes())

    def test_changed_copy_aborts_before_signing(self):
        destination = self.root / "Prepared.app"
        plugin = self.root / "plugin.dylib"
        plugin.write_bytes(make_thin(text=b"\xc0\x03\x5f\xd6"))

        def corrupt_copy(*arguments):
            shutil.copytree(arguments[1], arguments[2], symlinks=True)
            core = Path(arguments[2]) / "Contents/Resources/wechat.dylib"
            changed = bytearray(core.read_bytes())
            changed[0x400] ^= 1
            core.write_bytes(changed)

        with patch("wechattool.prepare.run", side_effect=corrupt_copy), patch("wechattool.prepare.sign_copy") as sign:
            with self.assertRaises(CompatibilityError):
                prepare(self.app, destination, plugin)
            sign.assert_not_called()
        self.assertFalse(destination.exists())

    def test_changed_identity_aborts_before_signing(self):
        destination = self.root / "Prepared.app"
        plugin = self.root / "plugin.dylib"
        plugin.write_bytes(make_thin(text=b"\xc0\x03\x5f\xd6"))

        def changed_identity(*arguments):
            shutil.copytree(arguments[1], arguments[2], symlinks=True)
            info = {**self.info, "TeamIdentifier": "DIFFERENT0."}
            (Path(arguments[2]) / "Contents/Info.plist").write_bytes(plistlib.dumps(info))

        with patch("wechattool.prepare.run", side_effect=changed_identity), patch("wechattool.prepare.sign_copy") as sign:
            with self.assertRaisesRegex(CompatibilityError, "identity changed"):
                prepare(self.app, destination, plugin)
            sign.assert_not_called()
        self.assertFalse(destination.exists())

    def test_official_system_extensions_are_excluded_from_staged_copy(self):
        extension = self.app / "Contents/PlugIns/WeChatMacShare.appex/Contents"
        extension.mkdir(parents=True)
        (extension / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "official.share"}))
        staged = self.root / "Staged.app"
        shutil.copytree(self.app, staged)
        self.assertEqual(isolate_helpers(staged, create_identity(self.info), self.root), ["WeChatMacShare.appex"])
        self.assertTrue(extension.is_dir())
        self.assertFalse((staged / "Contents/PlugIns/WeChatMacShare.appex").exists())

    def test_shared_helper_storage_permissions_are_rejected(self):
        helper = self.app / "Contents/XPCServices/Unsafe.xpc/Contents"
        helper.mkdir(parents=True)
        (helper / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "official.helper"}))
        grants = {"com.apple.security.application-groups": ["5A4RE8SF68.com.tencent.xinWeChat"]}
        result = subprocess.CompletedProcess([], 0, stdout=plistlib.dumps(grants))
        with patch("wechattool.prepare.run", return_value=result):
            with self.assertRaisesRegex(CompatibilityError, "independent storage permissions"):
                isolate_helpers(self.app, create_identity(self.info), self.root)

    def test_file_provider_keeps_only_its_new_host_group(self):
        extension = self.app / "Contents/PlugIns/WeChatFileProviderExtension.appex"
        (extension / "Contents").mkdir(parents=True)
        identity = create_identity(self.info)
        metadata = {
            "CFBundleIdentifier": "com.tencent.xinWeChat.WeChatFileProviderExtension",
            "TeamIdentifier": "5A4RE8SF68.",
            "NSExtension": {
                "NSExtensionPointIdentifier": "com.apple.fileprovider-nonui",
                "NSExtensionFileProviderDocumentGroup": "5A4RE8SF68.com.tencent.xinWeChat",
            },
        }
        info = extension / "Contents/Info.plist"
        info.write_bytes(plistlib.dumps(metadata))
        source_grants = {"com.apple.security.app-sandbox": True,
                         "com.apple.security.application-groups": ["5A4RE8SF68.com.tencent.xinWeChat"]}
        expected = isolated_file_provider_entitlements(source_grants, identity)
        calls = []

        def codesign(*arguments):
            calls.append(arguments)
            grants = source_grants if len(calls) == 1 else expected
            return subprocess.CompletedProcess(arguments, 0, stdout=plistlib.dumps(grants))

        with patch("wechattool.prepare.run", side_effect=codesign):
            self.assertEqual(isolate_helpers(self.app, identity, self.root), [])
        result = plistlib.loads(info.read_bytes())
        self.assertEqual(result["CFBundleIdentifier"], identity.bundle_id + ".WeChatFileProviderExtension")
        self.assertEqual(result["NSExtension"]["NSExtensionFileProviderDocumentGroup"], identity.app_group)
        self.assertEqual(plistlib.loads((self.root / "file-provider-entitlements.plist").read_bytes()), expected)
        self.assertTrue(any("--sign" in call for call in calls))
        self.assertTrue(any("--verify" in call for call in calls))

    def test_unknown_system_extension_refused(self):
        (self.app / "Contents/PlugIns/Future.appex").mkdir(parents=True)
        with self.assertRaisesRegex(CompatibilityError, "Unsupported system extension"):
            isolate_helpers(self.app, create_identity(self.info), self.root)


if __name__ == "__main__":
    unittest.main()
