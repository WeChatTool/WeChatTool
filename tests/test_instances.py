from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import unittest

from wechattool.instances import (
    BUNDLE_ID_PREFIX, InstanceIdentity, create_identity, isolated_entitlements, isolated_info,
    isolated_file_provider_entitlements, isolated_file_provider_info,
)


class InstanceTests(unittest.TestCase):
    def setUp(self):
        self.info = {
            "CFBundleIdentifier": "com.tencent.xinWeChat",
            "CFBundleExecutable": "WeChat",
            "CFBundleName": "WeChat",
            "TeamIdentifier": "5A4RE8SF68.",
            "CFBundleURLTypes": [{"CFBundleURLSchemes": ["weixin", "wechat"]}],
            "SUEnableAutomaticChecks": True,
            "SUAutomaticallyUpdate": True,
            "SUAllowsAutomaticUpdates": True,
            "NSCameraUsageDescription": "Video calls",
            "UnrelatedMetadata": {"items": ["retained"]},
        }
        self.identity = create_identity(self.info, instance_id="0123456789abcdef" * 2)
        self.entitlements = {
            "com.apple.application-identifier": "5A4RE8SF68.com.tencent.xinWeChat",
            "com.apple.developer.team-identifier": "5A4RE8SF68",
            "application-identifier": "5A4RE8SF68.com.tencent.xinWeChat",
            "com.apple.security.app-sandbox": True,
            "com.apple.security.application-groups": [
                "5A4RE8SF68.com.tencent.xinWeChat", "5A4RE8SF68.someSharedGroup"],
            "keychain-access-groups": ["5A4RE8SF68.com.tencent.xinWeChat", "shared.keychain"],
            "com.apple.security.temporary-exception.mach-lookup.global-name": [
                "com.tencent.xinWeChat-spks", "com.tencent.xinWeChat-spki",
                "com.tencent.xinWeChat.other", "com.example.unrelated",
                "com.tencent.xinWeChatLookalike-spks"],
            "com.apple.security.device.camera": True,
            "com.apple.security.network.client": True,
            "com.apple.security.files.user-selected.read-write": True,
            "com.apple.security.temporary-exception.sbpl": [
                '(allow network-outbound (literal "/private/var/run/usbmuxd"))'],
        }

    def test_new_preparations_have_independent_immutable_identities(self):
        first, second = create_identity(self.info), create_identity(self.info)
        self.assertNotEqual(first.instance_id, second.instance_id)
        self.assertNotEqual(first.bundle_id, second.bundle_id)
        self.assertNotEqual(first.app_group, second.app_group)
        self.assertRegex(first.instance_id, r"^[0-9a-f]{32}$")
        self.assertEqual(first.bundle_id, BUNDLE_ID_PREFIX + first.instance_id)
        self.assertEqual(first.app_group, first.application_identifier)
        with self.assertRaises(FrozenInstanceError):
            first.bundle_id = "changed"
        metadata = first.to_dict()
        self.assertEqual(InstanceIdentity(**metadata), first)
        metadata["instance_id"] = "changed"
        self.assertNotEqual(metadata["instance_id"], first.instance_id)

    def test_main_metadata_is_private_named_and_does_not_claim_official_links(self):
        original = deepcopy(self.info)
        result = isolated_info(self.info, self.identity, "工作微信")
        self.assertEqual(result["CFBundleIdentifier"], self.identity.bundle_id)
        self.assertEqual(result["CFBundleName"], "工作微信")
        self.assertEqual(result["CFBundleDisplayName"], "工作微信")
        self.assertEqual(result["TeamIdentifier"], self.info["TeamIdentifier"])
        self.assertEqual(result["WeChatToolInstanceID"], self.identity.instance_id)
        self.assertEqual(result["WeChatToolSourceBundleIdentifier"], "com.tencent.xinWeChat")
        self.assertNotIn("CFBundleURLTypes", result)
        for key in ("SUEnableAutomaticChecks", "SUAutomaticallyUpdate", "SUAllowsAutomaticUpdates"):
            self.assertIs(result[key], False)
        self.assertEqual(result["CFBundleExecutable"], "WeChat")
        self.assertEqual(result["NSCameraUsageDescription"], self.info["NSCameraUsageDescription"])
        result["UnrelatedMetadata"]["items"].append("copy only")
        self.assertEqual(self.info, original)

    def test_entitlements_replace_shared_grants_and_preserve_capabilities(self):
        original = deepcopy(self.entitlements)
        result = isolated_entitlements(self.entitlements, self.identity)
        for key in ("com.apple.application-identifier", "application-identifier"):
            self.assertEqual(result[key], self.identity.application_identifier)
        self.assertEqual(result["com.apple.security.application-groups"], [self.identity.app_group])
        self.assertEqual(result["keychain-access-groups"], [self.identity.application_identifier])
        self.assertNotIn("com.apple.developer.team-identifier", result)
        self.assertEqual(result["com.apple.security.temporary-exception.mach-lookup.global-name"], [
            self.identity.bundle_id + "-spks", self.identity.bundle_id + "-spki",
            self.identity.bundle_id + ".other", "com.example.unrelated",
            "com.tencent.xinWeChatLookalike-spks"])
        for key in ("com.apple.security.app-sandbox", "com.apple.security.device.camera",
                    "com.apple.security.network.client", "com.apple.security.files.user-selected.read-write"):
            self.assertIs(result[key], True)
        self.assertEqual(result["com.apple.security.temporary-exception.sbpl"], original["com.apple.security.temporary-exception.sbpl"])
        result["com.apple.security.temporary-exception.sbpl"].append("copy only")
        self.assertEqual(self.entitlements, original)
        self.assertNotIn("com.apple.security.cs.disable-library-validation", result)

    def test_optional_grants_are_not_invented(self):
        result = isolated_entitlements({"com.apple.security.app-sandbox": True}, self.identity)
        self.assertEqual(result, {
            "com.apple.security.app-sandbox": True,
            "com.apple.application-identifier": self.identity.application_identifier,
            "com.apple.security.application-groups": [self.identity.app_group],
        })

    def test_invalid_source_metadata_and_ids_are_rejected(self):
        for value in (None, [], "plist", 1):
            with self.subTest(source=value), self.assertRaises(ValueError):
                create_identity(value)
        for value in (None, 1, [], "", "com.tencent.xinWeChat.copy", self.identity.bundle_id):
            info = self.info | {"CFBundleIdentifier": value}
            with self.subTest(source_id=value), self.assertRaises(ValueError):
                create_identity(info)
        for value in (None, 1, [], "", "5A4RE8SF68", "5A4RE8SF68..", "5A4RE8SF6.", "5A4RE8SF6/.", "5A4RE8SF68.\n"):
            with self.subTest(team=value), self.assertRaises(ValueError):
                create_identity(self.info | {"TeamIdentifier": value})
        for value in (True, 1, [], "", "a" * 31, "a" * 33, "A" * 32, "z" * 32, "a" * 32 + "\n"):
            with self.subTest(instance_id=value), self.assertRaises(ValueError):
                create_identity(self.info, instance_id=value)

    def test_inconsistent_or_forged_identity_is_rejected(self):
        for changes in ({"bundle_id": "com.tencent.xinWeChat"}, {"instance_id": "f" * 32},
                        {"source_bundle_id": None}, {"source_bundle_id": "other"},
                        {"team_identifier": "other"}, {"app_group": "shared.group"}, {"app_group": None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.identity, **changes)
        with self.assertRaises(ValueError):
            isolated_info(self.info | {"TeamIdentifier": "OTHERTEAM1."}, self.identity, "Copy")
        for identity in (None, {}, self.identity.to_dict()):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                isolated_info(self.info, identity, "Copy")
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                isolated_entitlements(self.entitlements, identity)

    def test_invalid_display_names_are_rejected(self):
        for name in (None, 1, [], "", " \t ", "../Copy", "Copy\x00", "Copy\n", "Copy\x7f"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                isolated_info(self.info, self.identity, name)

    def test_invalid_or_unsandboxed_entitlements_are_rejected(self):
        for value in (None, [], "plist", 1):
            with self.subTest(entitlements=value), self.assertRaises(ValueError):
                isolated_entitlements(value, self.identity)
        for sandbox in (None, False, 0, 1, "true"):
            with self.subTest(sandbox=sandbox), self.assertRaises(ValueError):
                isolated_entitlements({"com.apple.security.app-sandbox": sandbox}, self.identity)
        for key in ("com.apple.application-identifier", "application-identifier"):
            for value in (None, 1, [], "", "other.app", self.identity.application_identifier):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    isolated_entitlements(self.entitlements | {key: value}, self.identity)
        for key in ("com.apple.security.application-groups", "keychain-access-groups",
                    "com.apple.security.temporary-exception.mach-lookup.global-name"):
            for value in (None, "shared.group", [1], [""], {"group": "shared"}):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    isolated_entitlements(self.entitlements | {key: value}, self.identity)


class FileProviderInstanceTests(unittest.TestCase):
    def setUp(self):
        self.source_group = "5A4RE8SF68.com.tencent.xinWeChat"
        self.identity = create_identity({
            "CFBundleIdentifier": "com.tencent.xinWeChat", "TeamIdentifier": "5A4RE8SF68.",
        }, instance_id="0123456789abcdef" * 2)
        self.info = {
            "CFBundleIdentifier": "com.tencent.xinWeChat.WeChatFileProviderExtension",
            "CFBundleExecutable": "WeChatFileProviderExtension",
            "TeamIdentifier": "5A4RE8SF68.",
            "NSExtension": {
                "NSExtensionFileProviderDocumentGroup": self.source_group,
                "NSExtensionFileProviderSupportsEnumeration": True,
                "NSExtensionPointIdentifier": "com.apple.fileprovider-nonui",
                "NSExtensionPrincipalClass": "FileProviderExtension",
            },
        }
        self.entitlements = {
            "com.apple.security.app-sandbox": True,
            "com.apple.security.application-groups": [self.source_group],
        }

    def test_file_provider_uses_the_same_group_as_its_private_host(self):
        original = deepcopy(self.info)
        result = isolated_file_provider_info(self.info, self.identity)
        self.assertEqual(result["CFBundleIdentifier"], self.identity.bundle_id + ".WeChatFileProviderExtension")
        # This is the documented static behavior of this extension's path helper:
        # strip its last bundle-ID component and prepend TeamIdentifier.
        computed_group = result["TeamIdentifier"] + result["CFBundleIdentifier"].rsplit(".", 1)[0]
        self.assertEqual(computed_group, self.identity.app_group)
        self.assertEqual(result["NSExtension"]["NSExtensionFileProviderDocumentGroup"], computed_group)
        self.assertEqual(result["CFBundleExecutable"], self.info["CFBundleExecutable"])
        self.assertIs(result["NSExtension"]["NSExtensionFileProviderSupportsEnumeration"], True)
        self.assertEqual(result["NSExtension"]["NSExtensionPrincipalClass"], "FileProviderExtension")
        result["NSExtension"]["NSExtensionPrincipalClass"] = "copy only"
        self.assertEqual(self.info, original)

    def test_duplicate_document_group_declarations_are_rewritten(self):
        self.info["NSExtensionFileProviderDocumentGroup"] = self.source_group
        self.info["NSExtension"]["NSExtensionAttributes"] = {
            "NSExtensionFileProviderDocumentGroup": self.source_group,
        }
        original = deepcopy(self.info)
        result = isolated_file_provider_info(self.info, self.identity)
        for node in (result, result["NSExtension"], result["NSExtension"]["NSExtensionAttributes"]):
            self.assertEqual(node["NSExtensionFileProviderDocumentGroup"], self.identity.app_group)
        self.assertEqual(self.info, original)

    def test_extension_entitlements_keep_only_the_private_host_group(self):
        original = deepcopy(self.entitlements)
        result = isolated_file_provider_entitlements(self.entitlements, self.identity)
        self.assertEqual(result, {
            "com.apple.security.app-sandbox": True,
            "com.apple.security.application-groups": [self.identity.app_group],
            "com.apple.application-identifier": self.identity.application_identifier + ".WeChatFileProviderExtension",
        })
        result["com.apple.security.application-groups"].append("copy only")
        self.assertEqual(self.entitlements, original)

    def test_optional_extension_identity_and_capabilities_are_rewritten(self):
        source_app_id = self.source_group + ".WeChatFileProviderExtension"
        self.entitlements.update({
            "application-identifier": source_app_id,
            "com.apple.application-identifier": source_app_id,
            "com.apple.developer.team-identifier": "5A4RE8SF68",
            "keychain-access-groups": [self.source_group, "old.shared.group"],
            "com.apple.security.network.client": True,
            "com.apple.security.files.user-selected.read-write": True,
        })
        original = deepcopy(self.entitlements)
        result = isolated_file_provider_entitlements(self.entitlements, self.identity)
        new_app_id = self.identity.application_identifier + ".WeChatFileProviderExtension"
        self.assertEqual(result["com.apple.application-identifier"], new_app_id)
        self.assertEqual(result["application-identifier"], new_app_id)
        self.assertEqual(result["keychain-access-groups"], [new_app_id])
        self.assertNotIn("com.apple.developer.team-identifier", result)
        self.assertIs(result["com.apple.security.network.client"], True)
        self.assertIs(result["com.apple.security.files.user-selected.read-write"], True)
        self.assertEqual(self.entitlements, original)

    def test_invalid_extension_metadata_is_rejected(self):
        for value in (None, [], "plist", 1):
            with self.subTest(source=value), self.assertRaises(ValueError):
                isolated_file_provider_info(value, self.identity)
        for key, values in {
            "CFBundleIdentifier": [None, [], 1, self.identity.bundle_id, "com.tencent.xinWeChat.OtherExtension"],
            "TeamIdentifier": [None, [], 1, "OTHERTEAM1."],
            "NSExtension": [None, [], 1, {}],
        }.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    isolated_file_provider_info(self.info | {key: value}, self.identity)
        for key, values in {
            "NSExtensionPointIdentifier": [None, 1, [], "com.apple.share-services"],
            "NSExtensionFileProviderDocumentGroup": [None, [], 1, "shared.group", self.identity.app_group],
            "NSExtensionAttributes": [None, [], 1, "attributes"],
        }.items():
            for value in values:
                info = deepcopy(self.info)
                info["NSExtension"][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    isolated_file_provider_info(info, self.identity)
        for location in ("root", "attributes"):
            for value in (None, [], 1, "shared.group"):
                info = deepcopy(self.info)
                node = info if location == "root" else info["NSExtension"].setdefault("NSExtensionAttributes", {})
                node["NSExtensionFileProviderDocumentGroup"] = value
                with self.subTest(location=location, value=value), self.assertRaises(ValueError):
                    isolated_file_provider_info(info, self.identity)

    def test_invalid_extension_grants_and_identity_are_rejected(self):
        for value in (None, [], "plist", 1):
            with self.subTest(source=value), self.assertRaises(ValueError):
                isolated_file_provider_entitlements(value, self.identity)
        for value in (None, False, 0, 1, "true"):
            with self.subTest(sandbox=value), self.assertRaises(ValueError):
                isolated_file_provider_entitlements(self.entitlements | {"com.apple.security.app-sandbox": value}, self.identity)
        for value in (None, [], "shared.group", [1], [""], ["shared.group"],
                      [self.source_group, "shared.group"], [self.identity.app_group]):
            with self.subTest(groups=value), self.assertRaises(ValueError):
                isolated_file_provider_entitlements(self.entitlements | {"com.apple.security.application-groups": value}, self.identity)
        for key in ("application-identifier", "com.apple.application-identifier"):
            for value in (None, [], 1, "shared.app", self.source_group):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    isolated_file_provider_entitlements(self.entitlements | {key: value}, self.identity)
        for identity in (None, {}, self.identity.to_dict()):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                isolated_file_provider_info(self.info, identity)
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                isolated_file_provider_entitlements(self.entitlements, identity)


if __name__ == "__main__":
    unittest.main()
