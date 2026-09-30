"""Synthetic-only Linux installer tests; no installed game is accessed."""
import hashlib
import importlib.util
import json
import os
import platform
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("cfi", HERE / "c_force_installer.py")
assert spec is not None and spec.loader is not None
cfi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cfi)


def digest(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class CForceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.home = self.root / "Dom żółw"; self.game = self.home / ".steam/steam/steamapps/common/Crypt Custodian"
        self.game.mkdir(parents=True)
        app = self.home / ".steam/steam/steamapps/appmanifest_2394650.acf"
        app.write_text('"appid" "2394650"', encoding="utf8")
        for n, data in (("data.win", b"old-data"), ("translations.ini", b"old-ini")):
            (self.game / n).write_bytes(data)
        self.package = self.root / "Paczka ze spacją"; (self.package / "GameFiles").mkdir(parents=True)
        payload = {"data.win": b"new-data", "translations.ini": b"new-ini"}
        for n, data in payload.items(): (self.package / "GameFiles" / n).write_bytes(data)
        for n in cfi.PACKAGE_FILES - {"GameFiles/data.win", "GameFiles/translations.ini", "release-manifest.json", "SHA256SUMS.txt"}:
            p = self.package / n; p.parent.mkdir(parents=True, exist_ok=True); p.write_text("x", encoding="utf8")
        manifest = {"profile":"C-Force", "appId":"2394650", "version":"test", "payload":[{"name":n,"sha256":hashlib.sha256(v).hexdigest()} for n,v in payload.items()]}
        (self.package / "release-manifest.json").write_text(json.dumps(manifest), encoding="utf8")
        sums = "\n".join(digest(self.package / n) + "  " + n for n in sorted(cfi.PACKAGE_FILES - {"SHA256SUMS.txt"})) + "\n"
        (self.package / "SHA256SUMS.txt").write_text(sums, encoding="ascii")
        self.old_root, self.old_hash, self.old_fsync = cfi.ROOT, cfi.MANIFEST_SHA256, cfi.fsync_dir
        cfi.ROOT, cfi.MANIFEST_SHA256 = self.package, digest(self.package / "release-manifest.json")
        if platform.system() == "Windows": cfi.fsync_dir = lambda path: None

    def tearDown(self):
        cfi.ROOT, cfi.MANIFEST_SHA256, cfi.fsync_dir = self.old_root, self.old_hash, self.old_fsync; self.tmp.cleanup()

    def test_autodetect_unicode_space_and_transaction(self):
        self.assertEqual(cfi.candidates(self.home), [str(self.game.resolve())])
        manifest, desc = cfi.package_manifest(); state = cfi.state_root(self.home)
        (state / "backups").mkdir(); (state / "operations").mkdir()
        cfi.install(self.game, state, manifest, desc)
        self.assertEqual((self.game / "data.win").read_bytes(), b"new-data")
        cfi.uninstall(self.game, state)
        self.assertEqual((self.game / "data.win").read_bytes(), b"old-data")

    def test_multiple_matches_and_symlink_refused(self):
        other = self.home / ".local/share/Steam/steamapps/common/Crypt Custodian"; other.mkdir(parents=True)
        (other.parent.parent / "appmanifest_2394650.acf").write_text("2394650")
        self.assertEqual(len(cfi.candidates(self.home)), 2)
        if platform.system() == "Windows": self.skipTest("Windows host may forbid test symlinks")
        link = self.root / "link"; link.symlink_to(self.game, target_is_directory=True)
        with self.assertRaises(cfi.Refusal): cfi.safe_existing(link, directory=True)

    def test_incomplete_refused(self):
        state = cfi.state_root(self.home); (state / "backups").mkdir()
        bad = state / "backups/bad"; bad.mkdir(); (bad / "receipt.json").write_text("{}")
        with self.assertRaises(cfi.Refusal): cfi.incomplete(state, self.game)


if __name__ == "__main__": unittest.main(verbosity=2)
