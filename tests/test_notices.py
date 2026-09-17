import copy
import unittest
from unittest.mock import patch

from wechattool.notices import notice_adapter, notice_profiles


class NoticeCompatibilityTests(unittest.TestCase):
    def test_reviewed_images_enable_their_own_record_layout(self):
        for profile in notice_profiles():
            with self.subTest(arch=profile["arch"]):
                hook = {"arch": profile["arch"], "uuid": profile["uuid"],
                        "address": profile["predicate_address"], "image_sha256": profile["image_sha256"]}
                self.assertEqual(notice_adapter(hook, profile["version"], profile["build"]), profile["id"])

    def test_future_or_modified_images_keep_preservation_without_notices(self):
        profile = notice_profiles()[0]
        original = {"arch": profile["arch"], "uuid": profile["uuid"],
                    "address": profile["predicate_address"], "image_sha256": profile["image_sha256"]}
        for field, value in [("arch", "unknown"), ("uuid", "00000000-0000-0000-0000-000000000000"),
                             ("address", 1), ("image_sha256", "0" * 64)]:
            with self.subTest(field=field):
                changed = {**original, field: value}
                self.assertIsNone(notice_adapter(changed, profile["version"], profile["build"]))
        self.assertIsNone(notice_adapter(original, "future", profile["build"]))
        self.assertIsNone(notice_adapter(original, profile["version"], "future"))

    def test_ambiguous_or_unknown_layout_is_not_selected(self):
        profile = notice_profiles()[0]
        hook = {"arch": profile["arch"], "uuid": profile["uuid"],
                "address": profile["predicate_address"], "image_sha256": profile["image_sha256"]}
        with patch("wechattool.notices.notice_profiles", return_value=[profile, profile]):
            self.assertIsNone(notice_adapter(hook, profile["version"], profile["build"]))
        changed = copy.deepcopy(profile)
        changed["layout"] = "unreviewed-layout"
        with patch("wechattool.notices.notice_profiles", return_value=[changed]):
            self.assertIsNone(notice_adapter(hook, profile["version"], profile["build"]))
