"""Read-only audit for a built C-Force release; never accesses game files."""
import json
import re
import unittest
from pathlib import Path

from build_release import FILES, PROJECT, audit_directory, audit_zip
from verify_sources import NAME, PATCHED, digest

PACKAGE = PROJECT / "release" / NAME


@unittest.skipUnless((PACKAGE / "release-manifest.json").is_file(), "C-Force package required")
class PublicReleaseTests(unittest.TestCase):
    def test_c_force_profile_and_payload_pins(self):
        manifest = json.loads((PACKAGE / "release-manifest.json").read_text(encoding="ascii"))
        self.assertEqual(manifest["profile"], "C-Force")
        self.assertEqual(manifest["appId"], "2394650")
        self.assertEqual(manifest["platforms"], ["Windows x64", "Steam Deck/Proton"])
        self.assertEqual(manifest["uncertainEntries"], 125)
        self.assertTrue(manifest["forceReplacement"])
        self.assertEqual({p["name"]: p["sha256"] for p in manifest["payload"]}, PATCHED)
        for name, expected in PATCHED.items():
            self.assertEqual(digest(PACKAGE / "GameFiles" / name), expected)

    def test_closed_list_documents_and_archive(self):
        audit_directory(PACKAGE)
        readme = (PACKAGE / "CZYTAJ_MNIE.txt").read_text(encoding="utf-8-sig")
        for phrase in ("C-Force", "125 wpis?w", "nie jest atomowa", "Steam Deck"):
            self.assertIn(phrase, readme)
        self.assertRegex(
            readme,
            r"mo[?\u017c]e wi[?\u0119]c cofn[?\u0105][?\u0107] aktualizacj[?\u0119] gry",
        )
        self.assertNotIn("B-", readme)
        archive = PACKAGE.parent / (NAME + ".zip")
        self.assertEqual(len(audit_zip(PACKAGE, archive)), len(FILES))


if __name__ == "__main__":
    unittest.main(verbosity=2)
