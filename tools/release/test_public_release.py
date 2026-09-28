"""Final read-only release audit; real-game Preflight only with explicit flag.

No package/installation writes. The sole report is new in the approved test Temp.
"""
import json
import os
import re
import subprocess
import sys
import unittest
import uuid

from build_release import FILES, PROJECT, audit_directory, audit_zip, text_bytes, write_new
from verify_sources import EXE, NAME, PATCHED, TEMP, digest, private_path

PACKAGE = PROJECT / "release" / NAME
PREVIOUS = PROJECT / "release" / "superseded-595662b867d5"
OLD_SHA = "595662b867d5c9b0d7541348158f9bc6ef2ee2dc24c1de51a9b7aebc361600c2"
# Independent verbatim expectation from the user, not imported from the builder.
QUOTE = """I, as the developer and copyright holder of Crypt Custodian, grant permission to modify the game's data.win file for the purpose of creating and maintaining a fan-made translation/modification of the game.

I also grant permission to distribute the modified files as part of this fan-made project, provided that:

the project is distributed free of charge,

it is clearly stated that it is an unofficial fan-made modification,

no ownership of the original game or its assets is claimed,

the modified files are used only in connection with legally obtained copies of Crypt Custodian.

This permission specifically includes extracting, editing, replacing, and repackaging content contained within data.win where necessary for the modification."""
RESULTS = {}


def current_documents():
    return [
        (PACKAGE / name).read_text(encoding="utf-8-sig")
        for name in ("CZYTAJ_MNIE.txt", "LICENSE-NOTICE.txt")
    ]


@unittest.skipUnless((PACKAGE / "release-manifest.json").is_file(), "Local release package required")
class PublicReleaseTests(unittest.TestCase):
    def test_permission_and_document_consistency(self):
        manifest = json.loads((PACKAGE / "release-manifest.json").read_text(encoding="ascii"))
        self.assertEqual(manifest["version"], "1.0-beta")
        self.assertEqual(manifest["status"], "public-beta")
        self.assertEqual(manifest["technicalReadiness"], "ready")
        self.assertTrue(manifest["legalCopyRequired"])
        self.assertTrue(manifest["fanTranslation"])
        permission = manifest["distributionPermission"]
        self.assertEqual(permission["basis"], "user-supplied-permission-message")
        self.assertEqual(permission["claimedSenderRole"], "developer and copyright holder of Crypt Custodian")
        self.assertEqual(permission["quote"], QUOTE)
        self.assertEqual(permission["authoritativeLanguage"], "en")
        self.assertEqual(permission["fullQuoteLocation"], "LICENSE-NOTICE.txt")
        self.assertTrue(permission["polishSummaryIsExplanatoryOnly"])
        for key in ("independentlyVerifiedIdentity", "independentlyVerifiedAuthorship",
                    "independentlyVerifiedMessageAuthenticity", "broaderLicenseInferred"):
            self.assertIs(permission[key], False)
        license_text = (PACKAGE / "LICENSE-NOTICE.txt").read_text(encoding="utf-8-sig")
        self.assertEqual(license_text.count('"' + QUOTE + '"'), 1)
        self.assertIn(text_bytes('"' + QUOTE + '"'), (PACKAGE / "LICENSE-NOTICE.txt").read_bytes())
        for text in current_documents():
            self.assertIn("Treść zgody została przekazana przez użytkownika", text)
            self.assertIn("Publiczna beta, technicznie gotowa.", text)
            self.assertIn("Nie zweryfikowano niezależnie tożsamości nadawcy, autorstwa ani autentyczności wiadomości.", text)
            self.assertIn("CYTAT ANGIELSKI — TREŚĆ NADRZĘDNA", text)
            self.assertIn("wyłącznie objaśnieniem", text)
            self.assertIn("znaki towarowe należą", text)
            self.assertNotIn("Granted", text)
            self.assertNotRegex(text.lower(), r"dystrybucja (?:jest|pozostaje)\s+zablokowana")
        for name in ("CZYTAJ_MNIE.txt", "LICENSE-NOTICE.txt"):
            source = (PROJECT / "tools" / "release" / name).read_text(encoding="utf-8-sig")
            self.assertEqual((PACKAGE / name).read_bytes(), text_bytes(source, bom=True))
        RESULTS["verbatimEnglishQuote"] = "PASS; complete text and paragraph breaks, TXT CRLF and JSON round-trip"
        RESULTS["permissionProvenance"] = "user supplied; identity/authorship/authenticity not independently verified"

    def check_condition(self, key, english, polish):
        manifest = json.loads((PACKAGE / "release-manifest.json").read_text(encoding="ascii"))
        permission = manifest["distributionPermission"]
        self.assertIs(permission["conditions"][key], True)
        self.assertIn(english, permission["quote"])
        for text in current_documents():
            self.assertIn(polish, text)
        RESULTS.setdefault("permissionConditions", {})[key] = "PASS"

    def test_condition_free_of_charge_no_payment(self):
        self.check_condition("freeOfCharge", "the project is distributed free of charge,",
                             "Wydanie jest bezpłatne.")
        audit_directory(PACKAGE)  # no extra checkout/payment component
        texts = [(PACKAGE / n).read_text(encoding="utf-8-sig") for n in FILES
                 if not n.startswith("GameFiles/")]
        texts += current_documents()
        for text in texts:
            self.assertNotRegex(text.lower(), r"https?://|paypal|stripe|patreon|ko-fi|buymeacoffee|checkout|paywall|bitcoin|\bblik\b")
            self.assertNotRegex(text.lower(), r"\b\d+(?:[.,]\d{1,2})?\s*(?:pln|zł|usd|eur|gbp)\b|[€£]\s*\d|\$\s*\d")
            self.assertNotRegex(text.lower(), r"\b(?:cena|price)\s*[:=]|\b(?:zapłać|kup teraz|buy now|donate)\b")
        RESULTS["paymentAudit"] = "PASS; public text/scripts scanned, closed file list and unchanged offline installer reviewed; immutable game assets are not a sales offer"

    def test_condition_unofficial_fan_modification(self):
        self.check_condition("clearlyUnofficialFanMade",
                             "it is clearly stated that it is an unofficial fan-made modification,",
                             "Jest to nieoficjalna, fanowska modyfikacja.")
        for text in current_documents():
            self.assertNotRegex(text.lower(), r"(?:^|\n)\s*(?:oficjalne wydanie|official release)|(?:zatwierdzone przez|endorsed by)")

    def test_condition_no_game_or_asset_ownership_claim(self):
        self.check_condition("noOwnershipClaimToOriginalGameOrAssets",
                             "no ownership of the original game or its assets is claimed,",
                             "Nie rościmy sobie praw własności do oryginalnej gry ani jej assetów (zasobów).")

    def test_condition_legally_obtained_copies_only(self):
        self.check_condition("modifiedFilesOnlyWithLegallyObtainedCopies",
                             "the modified files are used only in connection with legally obtained copies of Crypt Custodian.",
                             "Zmodyfikowane pliki wolno używać wyłącznie z legalnie uzyskanymi kopiami Crypt Custodian.")
        RESULTS["legalCopyRequirement"] = "documented use restriction; hashes check compatibility, not purchase/license ownership"

    @unittest.skipUnless(PREVIOUS.is_dir(), "Historical previous release required")
    def test_unchanged_payloads_and_installer_logic(self):
        old = PREVIOUS / NAME
        old_manifest = digest(old / "release-manifest.json")
        new_manifest = digest(PACKAGE / "release-manifest.json")
        self.assertNotEqual(old_manifest, new_manifest)
        for name in ("instaluj.bat", "odinstaluj.bat", "odinstaluj.ps1"):
            self.assertEqual((old / name).read_bytes(), (PACKAGE / name).read_bytes(), name)
        before = (old / "instaluj.ps1").read_bytes()
        self.assertEqual(before.count(old_manifest.encode("ascii")), 1)
        self.assertEqual((PACKAGE / "instaluj.ps1").read_bytes(),
                         before.replace(old_manifest.encode("ascii"), new_manifest.encode("ascii")))
        for name, expected in PATCHED.items():
            self.assertEqual(digest(PACKAGE / "GameFiles" / name), expected)
            self.assertEqual((old / "GameFiles" / name).read_bytes(),
                             (PACKAGE / "GameFiles" / name).read_bytes())
        RESULTS["installerOnlyChange"] = "pinned-manifest-sha256"
        RESULTS["payloadsByteIdenticalToPrevious"] = True

    @unittest.skipUnless(PREVIOUS.is_dir(), "Historical previous release required")
    def test_zip_reports_and_preserved_revision(self):
        for parent, expected in ((PREVIOUS, OLD_SHA), (PACKAGE.parent, None)):
            archive = parent / (NAME + ".zip")
            report = json.loads((parent / (NAME + ".build-report.json")).read_text(encoding="utf-8"))
            actual = digest(archive)
            if expected:
                self.assertEqual(actual, expected)
                self.assertIn(expected, (parent / "SUPERSEDED.txt").read_text(encoding="utf-8-sig"))
            self.assertEqual(report["sha256"], actual)
            self.assertEqual(report["bytes"], archive.stat().st_size)
            self.assertTrue(report["deterministicByteIdentical"])
            self.assertEqual((parent / (NAME + ".zip.sha256.txt")).read_text(encoding="ascii").splitlines(),
                             [f"{actual}  {archive.name}", f"bytes: {archive.stat().st_size}"])
            audit_directory(parent / NAME)
            self.assertEqual(audit_zip(parent / NAME, archive), report["entries"])
            if expected is None:
                manifest = json.loads((PACKAGE / "release-manifest.json").read_text(encoding="ascii"))
                for key in ("status", "technicalReadiness", "distributionPermission", "payload"):
                    self.assertEqual(report[key], manifest[key])
                self.assertEqual(report["manifestSha256"], digest(PACKAGE / "release-manifest.json"))
                self.assertEqual(report["sha256sumsSha256"], digest(PACKAGE / "SHA256SUMS.txt"))
                RESULTS["archiveSha256"] = actual
                RESULTS["archiveBytes"] = archive.stat().st_size

    def test_no_private_build_paths(self):
        # Include full immutable payloads and external metadata, not private reports.
        paths = [PACKAGE / n for n in FILES] + [
            PACKAGE.parent / (NAME + ".zip.sha256.txt"),
            PACKAGE.parent / (NAME + ".build-report.json"),
        ]
        for path in paths:
            raw = path.read_bytes().lower()
            for encoding in ("utf-8", "utf-16le"):
                self.assertFalse(private_path(raw.decode(encoding, errors="ignore")), path.name)
            if path.parent.name != "GameFiles":
                self.assertIsNone(re.search(rb"[a-z]:[\\/]+users[\\/]", raw), path.name)
        RESULTS["privatePathScan"] = "package-including-payloads-and-external-metadata; known-private-tokens-utf8-utf16le"

    @unittest.skipUnless("--preflight" in sys.argv, "explicit --preflight required")
    def test_real_installation_preflight_read_only(self):
        game = PROJECT.parent

        def snapshot():
            files = {}
            directories = []
            for path in game.iterdir():
                if path.is_file():
                    stat = path.stat()
                    files[path.name] = (stat.st_size, stat.st_mtime_ns, digest(path))
                else:
                    directories.append(path.name)
            return files, sorted(directories)

        before = snapshot()
        self.assertEqual(before[0]["CryptCustodian.exe"][2], EXE)
        for name, expected in PATCHED.items():
            self.assertEqual(before[0][name][2], expected)
        command = f'""{PACKAGE / "instaluj.bat"}" -Action Preflight -NonInteractive -GamePath "{game}""'
        result = subprocess.run(f'"{os.environ["COMSPEC"]}" /d /v:off /s /c {command}',
                                capture_output=True, timeout=120)
        self.assertEqual(snapshot(), before, "Game root files/directory names changed")
        self.assertEqual(result.returncode, 0, repr(result.stdout + result.stderr))
        self.assertIn(b"preflight:patched", result.stdout)
        RESULTS["realPreflight"] = {"status": "preflight:patched", "exitCode": 0,
                                    "rootFileHashesSizesMtimesAndDirectoryNamesUnchanged": True,
                                    "gameStarted": False, "installOrUninstallRun": False}


if __name__ == "__main__":
    if sys.argv[1:] not in ([], ["--preflight"]):
        raise SystemExit("Usage: test_public_release.py [--preflight]")
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(PublicReleaseTests))
    RESULTS.update(tests=result.testsRun, failures=len(result.failures), errors=len(result.errors),
                   skipped=len(result.skipped))
    root = TEMP / ("ccpl-public-release-tests-" + uuid.uuid4().hex)
    root.mkdir(exist_ok=False)
    write_new(root / "tests.json", text_bytes(json.dumps(RESULTS, indent=2) + "\n"))
    print(json.dumps(RESULTS, indent=2))
    print("REPORT=" + str(root / "tests.json"))
    raise SystemExit(not result.wasSuccessful())
