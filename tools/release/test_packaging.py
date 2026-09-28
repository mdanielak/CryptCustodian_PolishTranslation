"""Small synthetic ZIP tests. No extraction and no real-game writes."""
import json
import struct
import unittest
import uuid
import zipfile

from build_release import FILES, NAME, audit_directory, audit_zip, make_zip, validate_names, write_new
from verify_sources import TEMP, digest


ROOT = TEMP / ("ccpl-zip-tests-" + uuid.uuid4().hex)


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / self._testMethodName
        self.root.mkdir(parents=True, exist_ok=False)
        self.package = self.root / "package"
        self.package.mkdir()
        for name in FILES:
            path = self.package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if name != "SHA256SUMS.txt":
                write_new(path, (name + " zażółć gęślą jaźń\r\n").encode("utf-8"))
        sums = "".join(f"{digest(self.package / n)}  {n}\n" for n in FILES if n != "SHA256SUMS.txt")
        write_new(self.package / "SHA256SUMS.txt", sums.encode("ascii"))
        self.archive = self.root / "test.zip"

    def test_roundtrip_crc_sha_bytes_and_determinism(self):
        make_zip(self.package, self.archive)
        self.assertEqual(len(audit_zip(self.package, self.archive)), 10)
        second = self.root / "second.zip"
        make_zip(self.package, second)
        self.assertEqual(self.archive.read_bytes(), second.read_bytes())
        audit_directory(self.package)

    def test_closed_lists_unsafe_actual_archives(self):
        good = [NAME + "/" + n for n in FILES]
        for index, extra in enumerate([
            "../escape", "/absolute", "C:/absolute", NAME + "/x:ads", NAME + "/a/../b",
            NAME + "/a\\b", NAME + "/a.", NAME + "/a ", NAME + "//a", NAME + "/extra.dll",
            good[0], good[0].swapcase(),
        ]):
            with self.subTest(entry=extra):
                archive = self.root / f"invalid-{index}.zip"
                with zipfile.ZipFile(archive, "x") as z:
                    for n in good + [extra]:
                        z.writestr(n, b"fixture")
                with self.assertRaises(ValueError):
                    audit_zip(self.package, archive)
        with self.assertRaises(ValueError):
            validate_names(good[:-1])

    def test_archive_byte_mismatch_even_with_valid_crc(self):
        make_zip(self.package, self.archive)
        with (self.package / FILES[0]).open("ab") as f:
            f.write(b"changed")
        with self.assertRaisesRegex(ValueError, "byte mismatch"):
            audit_zip(self.package, self.archive)

    def test_crc_corruption(self):
        make_zip(self.package, self.archive)
        with zipfile.ZipFile(self.archive) as z:
            first = z.infolist()[0]
        data = bytearray(self.archive.read_bytes())
        # Deliberately corrupt the expected CRC in both headers, not file bytes.
        offset = first.header_offset + 14
        data[offset:offset + 4] = struct.pack("<I", first.CRC ^ 1)
        central = data.index(b"PK\x01\x02")
        data[central + 16:central + 20] = struct.pack("<I", first.CRC ^ 1)
        corrupt = self.root / "bad-crc.zip"
        write_new(corrupt, data)
        with self.assertRaisesRegex(zipfile.BadZipFile, "CRC"):
            audit_zip(self.package, corrupt)

    def test_sha_list_corruption(self):
        p = self.package / "SHA256SUMS.txt"
        p.write_text("0" * 64 + "  instaluj.ps1\n", encoding="ascii")
        with self.assertRaisesRegex(ValueError, "SHA256SUMS"):
            audit_directory(self.package)

    def test_no_clobber_archive(self):
        make_zip(self.package, self.archive)
        before = digest(self.archive)
        with self.assertRaises(FileExistsError):
            make_zip(self.package, self.archive)
        self.assertEqual(before, digest(self.archive))

    def test_extra_file_refused(self):
        write_new(self.package / "original-backup.win", b"fixture")
        with self.assertRaisesRegex(ValueError, "allowlist"):
            audit_directory(self.package)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PackagingTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    write_new(ROOT / "tests.json", json.dumps({
        "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "syntheticOnly": True,
    }, indent=2).encode("utf-8"))
    print("REPORT=" + str(ROOT / "tests.json"))
    raise SystemExit(not result.wasSuccessful())
