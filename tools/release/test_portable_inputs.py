"""Portable release configuration and pinned-input refusals; synthetic files only."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import build_release as build
import verify_sources as inputs


class PortableInputTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt" and shutil.which("powershell.exe"),
                         "Windows PowerShell required for synthetic installer tests")
    def test_installer_selftest_in_configured_temp(self):
        with tempfile.TemporaryDirectory(prefix="ccpl-selftest-host-") as tmp:
            root = Path(tmp)
            installer = (build.HERE / "installer.ps1").read_text(encoding="utf-8-sig")
            installer = installer.replace("__MANIFEST_SHA256__", "0" * 64)
            installer = installer.replace("__SELF_TEST_FUNCTIONS__",
                                          (build.HERE / "selftest.ps1").read_text(encoding="utf-8-sig"))
            script = root / "instaluj.ps1"
            build.write_new(script, build.text_bytes(installer, bom=True))
            result = subprocess.run(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                 "-File", str(script), "-SelfTest"],
                env={**os.environ, "TEMP": tmp, "TMP": tmp},
                capture_output=True, timeout=180)
            self.assertEqual(result.returncode, 0, (result.stdout, result.stderr))
            reports = list(root.glob("ccpl-selftest-*/tests.json"))
            self.assertEqual(len(reports), 1)
            report = json.loads(reports[0].read_text(encoding="utf-8"))
            self.assertTrue(report["syntheticOnly"])
            self.assertFalse(report["realGameStarted"])
            self.assertTrue(report["tests"])
            for case in report["tests"]:
                self.assertIn(case["status"], ("pass", "skip"), case)
                if case["status"] == "skip":
                    print("SelfTest SKIP:", case["name"], case.get("reason", ""))

    def parse(self, argv):
        parser = argparse.ArgumentParser()
        inputs.input_arguments(parser)
        return inputs.sources_from_args(parser.parse_args(argv))

    def test_explicit_inputs_and_env_precedence(self):
        with patch.dict(os.environ, {"CC_RELEASE_DATA": "env/data.win",
                                     "CC_RELEASE_TRANSLATIONS": "env/translations.ini"}):
            paths = self.parse(["--data", "fixture/zażółć/data.win"])
            self.assertEqual(paths["data.win"], Path("fixture/zażółć/data.win").absolute())
            self.assertEqual(paths["translations.ini"], Path("env/translations.ini").absolute())

    def test_inputs_required_no_installation_fallback(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(SystemExit) as error:
            self.parse([])
        self.assertEqual(error.exception.code, 2)

    def test_wrong_hash_refused_before_output_reservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = {name: root / name for name in inputs.PATCHED}
            for path in paths.values():
                path.write_bytes(b"not a release payload")
            target = root / "package"
            with self.assertRaisesRegex(ValueError, "SHA mismatch"):
                build.prepare(target, paths)
            self.assertFalse(target.exists())

    def test_source_allowlist(self):
        for sources in ({}, {"other.bin": Path("other.bin")}):
            with self.assertRaisesRegex(ValueError, "exactly"):
                inputs.verify(sources)

    def test_generic_private_path_detection(self):
        # Synthetic identities, never the developer's actual profile/library paths.
        for text in ("C:/Users/example/file", "D:/SteamLibrary/game", r"C:\\Users\\example\\file"):
            self.assertTrue(inputs.private_path(text), text)
        for text in ("cc-font-atlas/v1", "GameFiles/data.win", "ąćęłńóśźż"):
            self.assertFalse(inputs.private_path(text), text)


if __name__ == "__main__":
    unittest.main()
