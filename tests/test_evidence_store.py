import os
import sys
import shutil
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import browser.evidence as evidence


class TestArtifactRoute(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._patcher = mock.patch.object(evidence, "ARTIFACT_DIR", self._tmpdir)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_save_and_resolve_round_trip(self):
        artifact_id = evidence.save_screenshot("run1", "T1_after", b"\x89PNGfakebytes")
        self.assertIsNotNone(artifact_id)
        path = evidence.artifact_path(artifact_id)
        self.assertIsNotNone(path)
        self.assertTrue(os.path.isfile(path))

    def test_load_screenshot_bytes_round_trip(self):
        original = b"\x89PNGsomefakebytes"
        artifact_id = evidence.save_screenshot("run1", "T2_after", original)
        data = evidence.load_screenshot_bytes(artifact_id)
        self.assertEqual(data, original)

    def test_never_returns_raw_filesystem_path_for_unknown_id(self):
        self.assertIsNone(evidence.artifact_path("does_not_exist.png"))

    def test_path_traversal_rejected(self):
        # Never expose C:\Users\... or /etc/passwd-style access via the ID.
        self.assertIsNone(evidence.artifact_path("../../../../etc/passwd"))
        self.assertIsNone(evidence.artifact_path("..\\..\\windows\\system32\\config"))

    def test_absolute_path_injection_rejected(self):
        self.assertIsNone(evidence.artifact_path("/etc/passwd"))

    def test_empty_bytes_not_saved(self):
        self.assertIsNone(evidence.save_screenshot("run1", "empty", b""))

    def test_load_screenshot_bytes_unknown_id_returns_none(self):
        self.assertIsNone(evidence.load_screenshot_bytes("nope.png"))


if __name__ == "__main__":
    unittest.main()
