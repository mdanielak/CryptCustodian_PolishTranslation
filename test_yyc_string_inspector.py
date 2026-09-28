"""Synthetic-only tests. Prepared, NOT executed as part of this change.

All filesystem writes made by these tests are inside TemporaryDirectory.
Never reads the game or invokes an executable/subprocess.
"""

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import types
import unittest
from typing import cast
from unittest import mock

import yyc_string_inspector as inspector


BASE = 0x140000000
PE = 0x80
OPTIONAL = PE + 24
TABLE = OPTIONAL + 0xF0
TEXT = 0x200
RDATA = 0x400


def p16(data, offset, value):
    struct.pack_into("<H", data, offset, value)


def p32(data, offset, value):
    struct.pack_into("<I", data, offset, value)


def put(data, offset, payload):
    data[offset:offset + len(payload)] = payload


def instruction(data, offset, target_rva, *, rex=0x48, opcode=0x8D, modrm=0x05):
    rva = 0x1000 + offset - TEXT
    put(data, offset, bytes((rex, opcode, modrm))
        + struct.pack("<i", target_rva - (rva + 7)))


def fixture():
    """Minimal PE32+ with two sections, all terms, LEA and MOV candidates."""
    data = bytearray(0x800)
    put(data, 0, b"MZ")
    p32(data, 0x3C, PE)
    put(data, PE, b"PE\0\0")
    p16(data, PE + 4, 0x8664)
    p16(data, PE + 6, 2)
    p16(data, PE + 20, 0xF0)
    p16(data, PE + 22, 0x22)
    p16(data, OPTIONAL, 0x20B)
    p32(data, OPTIONAL + 16, 0x1010)
    struct.pack_into("<Q", data, OPTIONAL + 24, BASE)
    p32(data, OPTIONAL + 32, 0x1000)
    p32(data, OPTIONAL + 36, 0x200)
    p32(data, OPTIONAL + 56, 0x3000)
    p32(data, OPTIONAL + 60, 0x200)
    p32(data, OPTIONAL + 108, 16)
    put(data, TABLE, b".text\0\0\0")
    struct.pack_into("<IIII", data, TABLE + 8, 0x200, 0x1000, 0x200, TEXT)
    p32(data, TABLE + 36, 0x60000020)
    put(data, TABLE + 40, b".rdata\0\0")
    struct.pack_into("<IIII", data, TABLE + 48, 0x300, 0x2000, 0x400, RDATA)
    p32(data, TABLE + 76, 0x40000040)
    put(data, TEXT, b"\x90" * 0x200)
    put(data, RDATA, b"translations.ini\0")
    put(data, 0x440, b"english\0")  # no xref
    put(data, 0x481, "polish\0".encode("utf-16le"))  # deliberately odd file offset
    put(data, 0x4B0, b"ini_open\0")
    put(data, 0x4D0, b"language\0")
    put(data, 0x500, b"translations - Copy.ini\0")
    put(data, 0x540, b"translations2.ini\0")
    put(data, 0x580, b"ini_read_string\0")
    put(data, 0x600, b"translations.ini\0")  # retain duplicate occurrence
    instruction(data, 0x210, 0x2000)
    instruction(data, 0x230, 0x2081, rex=0x4C, opcode=0x8B, modrm=0x0D)
    return data


def analyze(data=None):
    data = bytes(fixture() if data is None else data)
    return inspector.inspect_snapshot(data, "synthetic/CryptCustodian.exe",
                                      hashlib.sha256(data).hexdigest())


class ParserTests(unittest.TestCase):
    def test_all_terms_duplicates_no_xref_and_section_flags(self):
        report = analyze()
        self.assertEqual(report["literal_count"], 9)
        self.assertEqual(report["xref_count"], 2)
        self.assertEqual(set(report["terms"]), set(inspector.TERMS))
        self.assertTrue(all(report["term_counts"].values()))
        self.assertEqual(report["term_counts"]["translations.ini"], 2)
        english = next(h for h in report["literals"] if h["term"] == "english")
        self.assertEqual(english["xref_ids"], [])
        self.assertEqual(english["section_name"], ".rdata")
        self.assertFalse(english["section_flags"]["executable"])
        self.assertTrue(english["section_flags"]["initialized_data"])
        self.assertFalse(report["truncated"])

    def test_lea_exact_offsets_and_preferred_va(self):
        ref = analyze()["xrefs"][0]
        self.assertEqual((ref["mnemonic"], ref["opcode"], ref["rex"], ref["modrm"]),
                         ("LEA", "8d", "48", "05"))
        self.assertEqual((ref["file_offset"], ref["rva"], ref["va"]),
                         (0x210, 0x1010, BASE + 0x1010))
        self.assertEqual((ref["target_file_offset"], ref["target_rva"], ref["target_va"]),
                         (RDATA, 0x2000, BASE + 0x2000))
        self.assertEqual(ref["disp32"], 0x2000 - 0x1017)
        self.assertEqual(ref["register"], "rax")

    def test_mov_extended_register_and_odd_utf16(self):
        report = analyze()
        hit = next(h for h in report["literals"] if h["term"] == "polish")
        self.assertEqual((hit["file_offset"], hit["encoding"]), (0x481, "utf-16le"))
        self.assertEqual(hit["byte_length"], 12)
        ref = report["xrefs"][hit["xref_ids"][0]]
        self.assertEqual((ref["mnemonic"], ref["opcode"], ref["register"]), ("MOV", "8b", "r9"))
        self.assertEqual(ref["literal_ids"], [hit["id"]])

    def test_every_term_in_both_encodings(self):
        data = fixture()
        data[RDATA:] = b"\0" * (len(data) - RDATA)
        offset = RDATA
        for term in inspector.TERMS:
            for codec in ("utf-8", "utf-16le"):
                payload = (term + "\0").encode(codec)
                put(data, offset, payload)
                offset += len(payload)
        report = analyze(data)
        self.assertEqual(report["literal_count"], 2 * len(inspector.TERMS))
        for term in inspector.TERMS:
            self.assertEqual({h["encoding"] for h in report["literals"] if h["term"] == term},
                             {"ascii/utf-8", "utf-16le"})

    def test_multiple_references_to_same_literal_are_not_collapsed(self):
        data = fixture()
        instruction(data, 0x260, 0x2000, opcode=0x8B)
        report = analyze(data)
        first = next(h for h in report["literals"] if h["file_offset"] == RDATA)
        duplicate = next(h for h in report["literals"] if h["file_offset"] == 0x600)
        self.assertEqual(len(first["xref_ids"]), 2)
        self.assertEqual(duplicate["xref_ids"], [])
        self.assertEqual({report["xrefs"][i]["file_offset"] for i in first["xref_ids"]},
                         {0x210, 0x260})

    def test_mapping_round_trip_and_raw_padding(self):
        image = inspector.PEImage(bytes(fixture()))
        for offset in (0, 0x1FF, 0x200, 0x3FF, 0x400, 0x700, 0x7FF):
            with self.subTest(offset=offset):
                rva = image.offset_to_rva(offset)
                assert rva is not None
                self.assertEqual(image.rva_to_offset(rva), offset)
                self.assertEqual(image.va_to_offset(BASE + rva), offset)
        self.assertTrue(image.location(0x700, 8)["includes_raw_padding"])
        self.assertIsNone(image.offset_to_rva(0x3FF, 2))
        self.assertIsNone(image.rva_to_offset(0x11FF, 2))
        self.assertIsNone(image.rva_to_offset(0x1800))
        self.assertIsNone(image.va_to_offset(BASE - 1))
        for offset, size in ((-1, 1), (len(image.data), 1), (0, 0)):
            with self.assertRaises(inspector.InspectorError):
                image.offset_to_rva(offset, size)

    def test_zero_fill_has_no_file_offset(self):
        data = fixture()
        p32(data, TABLE + 48, 0x600)
        image = inspector.PEImage(bytes(data))
        self.assertIsNone(image.rva_to_offset(0x2450))
        self.assertIsNone(image.va_to_offset(BASE + 0x2450))

    def test_literal_crossing_section_end_is_unmapped(self):
        data = fixture()
        put(data, 0x3FC, b"english\0")
        report = analyze(data)
        hit = next(h for h in report["literals"] if h["file_offset"] == 0x3FC)
        self.assertEqual(hit["region"], "cross_boundary")
        self.assertIsNone(hit["rva"])
        self.assertIsNone(hit["va"])
        self.assertEqual(hit["xref_ids"], [])

    def test_negative_displacement_and_executable_literal(self):
        data = fixture()
        put(data, 0x290, b"english\0")
        instruction(data, 0x2C0, 0x1090)
        report = analyze(data)
        ref = next(r for r in report["xrefs"] if r["file_offset"] == 0x2C0)
        self.assertLess(ref["disp32"], 0)
        hit = report["literals"][ref["literal_ids"][0]]
        self.assertEqual(hit["section_name"], ".text")
        self.assertTrue(hit["section_flags"]["executable"])

    def test_all_modrm_registers_and_rex_r(self):
        for rex in (0x48, 0x4C):
            for reg in range(8):
                with self.subTest(rex=rex, reg=reg):
                    data = fixture()
                    instruction(data, 0x210, 0x2000, rex=rex, modrm=(reg << 3) | 5)
                    self.assertEqual(analyze(data)["xrefs"][0]["register"],
                                     inspector.REGISTERS[reg + (8 if rex == 0x4C else 0)])

    def test_invalid_or_unsupported_instruction_forms(self):
        changes = ((0, 0x40), (0, 0x49), (0, 0x4F), (0, 0x90),
                   (1, 0x89), (1, 0xFF), (2, 0x04), (2, 0x45), (2, 0x85), (2, 0xC5))
        for index, value in changes:
            with self.subTest(index=index, value=value):
                data = fixture()
                data[0x210 + index] = value
                self.assertNotIn(0x210, [r["file_offset"] for r in analyze(data)["xrefs"]])

    def test_prefix_guard(self):
        for prefix in inspector.PREFIX_BYTES:
            with self.subTest(prefix=prefix):
                data = fixture()
                data[0x20F] = prefix
                self.assertNotIn(0x210, [r["file_offset"] for r in analyze(data)["xrefs"]])

    def test_no_scan_in_non_executable_section_or_across_end(self):
        data = fixture()
        # This byte pattern in .rdata would target its first literal if scanned.
        put(data, 0x680, b"\x48\x8d\x05" + struct.pack("<i", 0x2000 - 0x2287))
        # A would-be instruction extending past .text into .rdata is excluded.
        put(data, 0x3FD, b"\x48\x8d\x05")
        self.assertEqual(analyze(data)["xref_count"], 2)
        p32(data, TABLE + 36, 0x40000020)
        p32(data, OPTIONAL + 16, 0)  # no executable entrypoint now
        self.assertEqual(analyze(data)["xref_count"], 0)

    def test_target_must_be_start_of_literal(self):
        data = fixture()
        instruction(data, 0x210, 0x2001)
        self.assertEqual(analyze(data)["xref_count"], 1)

    def test_case_sensitive_full_tail_nul_and_suffix_annotation(self):
        data = fixture()
        put(data, 0x630, b"English\0english-more\0xenglish\0")
        put(data, 0x690, "polish-more\0Polish\0xpolish\0".encode("utf-16le"))
        report = analyze(data)
        english = [h for h in report["literals"] if h["term"] == "english"]
        polish = [h for h in report["literals"] if h["term"] == "polish"]
        self.assertEqual(len(english), 2)
        self.assertEqual(len(polish), 2)
        self.assertEqual(english[-1]["start_boundary"], "unproven")
        self.assertEqual(polish[-1]["start_boundary"], "unproven")
        self.assertEqual(report["literals"][0]["start_boundary"], "region_start")

    def test_unterminated_and_partial_utf16_nul_not_found(self):
        for payload in (b"english", "polish".encode("utf-16le") + b"\0"):
            with self.subTest(payload=payload):
                data = fixture() + payload
                self.assertEqual(analyze(data)["literal_count"], 9)

    def test_overlay_header_and_gap_are_not_given_section_vas(self):
        data = fixture()
        put(data, 0x40, b"english\0")
        p32(data, TABLE + 48 + 12, 0x600)  # relocate .rdata raw pointer, leave gap
        data[0x400:0x400] = b"\0" * 0x200
        put(data, 0x480, b"language\0")
        data.extend(b"translations.ini\0")
        report = analyze(data)
        by_offset = {h["file_offset"]: h for h in report["literals"]}
        self.assertEqual(by_offset[0x40]["region"], "headers")
        self.assertEqual(by_offset[0x40]["va"], BASE + 0x40)
        self.assertEqual(by_offset[0x480]["region"], "unmapped_gap")
        self.assertIsNone(by_offset[0x480]["va"])
        self.assertEqual(by_offset[0xA00]["region"], "overlay")
        self.assertIsNone(by_offset[0xA00]["va"])

    def test_no_strings_is_success_not_runtime_conclusion(self):
        data = fixture()
        data[RDATA:] = b"\0" * (len(data) - RDATA)
        report = analyze(data)
        self.assertEqual((report["literal_count"], report["xref_count"]), (0, 0))
        self.assertIn("pointer tables", " ".join(report["warnings"]))
        self.assertIn("reachability", " ".join(report["warnings"]))

    def test_truncated_headers_and_sections(self):
        data = fixture()
        for end in (0, 1, 63, PE, PE + 23, OPTIONAL + 111, TABLE + 79, 0x7FF):
            with self.subTest(end=end), self.assertRaises(inspector.InspectorError):
                inspector.PEImage(bytes(data[:end]))

    def test_bad_signatures_and_header_fields(self):
        mutations = (
            (0, b"NZ"), (PE, b"PX\0\0"), (0x3C, struct.pack("<I", 0xFFFFFFFC)),
            (0x3C, struct.pack("<I", 0x81)),
            (PE + 4, struct.pack("<H", 0x14C)),
            (PE + 6, struct.pack("<H", 0)), (PE + 6, struct.pack("<H", 97)),
            (PE + 20, struct.pack("<H", 111)), (PE + 22, struct.pack("<H", 0x102)),
            (OPTIONAL, struct.pack("<H", 0x10B)),
            (OPTIONAL + 32, struct.pack("<I", 0)),
            (OPTIONAL + 36, struct.pack("<I", 0x300)),
            (OPTIONAL + 56, struct.pack("<I", 0x2000)),
            (OPTIONAL + 60, struct.pack("<I", 0x100)),
            (OPTIONAL + 60, struct.pack("<I", 0x1000)),
            (OPTIONAL + 108, struct.pack("<I", 17)),
            (OPTIONAL + 16, struct.pack("<I", 0x2000)),
        )
        for offset, payload in mutations:
            with self.subTest(offset=offset, payload=payload):
                data = fixture()
                put(data, offset, payload)
                with self.assertRaises(inspector.InspectorError):
                    inspector.PEImage(bytes(data))

    def test_overlapping_out_of_bounds_or_misaligned_sections(self):
        mutations = (
            (TABLE + 60, 0x200),  # .rdata raw overlaps .text
            (TABLE + 60, 0),      # .rdata overlaps headers
            (TABLE + 60, 0x600),  # .rdata exceeds file
            (TABLE + 60, 0x401),  # unaligned raw
            (TABLE + 52, 0x1000), # virtual overlap
            (TABLE + 52, 0),      # virtual headers overlap
            (TABLE + 52, 0x2001), # unaligned RVA
            (TABLE + 52, 0xFFFFF000),
            (TABLE + 48, 0xFFFFFFFF),
            (TABLE + 56, 0xFFFFFFFF),
            (TABLE + 56, 0),      # empty raw with nonzero pointer
        )
        for offset, value in mutations:
            with self.subTest(offset=offset, value=value):
                data = fixture()
                p32(data, offset, value)
                with self.assertRaises(inspector.InspectorError):
                    inspector.PEImage(bytes(data))

    def test_virtual_alignment_tail_overlap(self):
        data = fixture()
        p32(data, TABLE + 8, 0x1001)
        with self.assertRaisesRegex(inspector.InspectorError, "Overlapping virtual"):
            inspector.PEImage(bytes(data))

    def test_directories_bounds_and_security_file_offset(self):
        data = fixture()
        struct.pack_into("<II", data, OPTIONAL + 112, 0x2000, 16)
        inspector.PEImage(bytes(data))
        for address, size in ((0x2000, 0), (0, 16), (0x2FFF, 16), (0x1800, 16)):
            broken = data.copy()
            struct.pack_into("<II", broken, OPTIONAL + 112, address, size)
            with self.assertRaises(inspector.InspectorError):
                inspector.PEImage(bytes(broken))
        data.extend(b"\0" * 16)
        struct.pack_into("<II", data, OPTIONAL + 112 + 4 * 8, 0x800, 16)
        inspector.PEImage(bytes(data))
        p32(data, OPTIONAL + 112 + 4 * 8, 0x400)
        with self.assertRaises(inspector.InspectorError):
            inspector.PEImage(bytes(data))

    def test_image_base_alignment_and_va_overflow(self):
        data = fixture()
        struct.pack_into("<Q", data, OPTIONAL + 24, BASE + 1)
        with self.assertRaises(inspector.InspectorError):
            inspector.PEImage(bytes(data))
        struct.pack_into("<Q", data, OPTIONAL + 24, 0xFFFFFFFFFFFF0000)
        p32(data, OPTIONAL + 56, 0x20000)
        with self.assertRaisesRegex(inspector.InspectorError, "VA overflow"):
            inspector.PEImage(bytes(data))

    def test_limits_fail_closed(self):
        for name, limit in (("MAX_SOURCE_BYTES", 1), ("MAX_SECTIONS", 1),
                            ("MAX_LITERALS", 8), ("MAX_XREFS", 1)):
            with self.subTest(name=name), mock.patch.object(inspector, name, limit):
                with self.assertRaises(inspector.InspectorError):
                    analyze()
        report = analyze()
        with mock.patch.object(inspector, "MAX_REPORT_BYTES", 32):
            with self.assertRaises(inspector.InspectorError):
                inspector.encode_report(report)

    def test_polish_json_round_trip_without_changing_literal_encoding(self):
        report = analyze()
        report["source"]["path"] = "C:/warsztat/ąćęłńóśźżĄĆĘŁŃÓŚŹŻ/🧹/CryptCustodian.exe"
        payload = inspector.encode_report(report)
        self.assertTrue(payload.isascii())
        self.assertEqual(json.loads(payload), report)
        self.assertEqual(json.loads(payload)["source"]["path"], report["source"]["path"])

    def test_exact_resource_limits_are_inclusive(self):
        with mock.patch.object(inspector, "MAX_SOURCE_BYTES", len(fixture())), \
                mock.patch.object(inspector, "MAX_SECTIONS", 2), \
                mock.patch.object(inspector, "MAX_LITERALS", 9), \
                mock.patch.object(inspector, "MAX_XREFS", 2):
            report = analyze()
        payload = inspector.encode_report(report)
        with mock.patch.object(inspector, "MAX_REPORT_BYTES", len(payload)):
            self.assertEqual(inspector.encode_report(report), payload)
        with mock.patch.object(inspector, "MAX_REPORT_BYTES", len(payload) - 1):
            with self.assertRaises(inspector.InspectorError):
                inspector.encode_report(report)


class FilesystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="yyc-synthetic-")
        self.addCleanup(self.temp.cleanup)
        # Resolve only the test sandbox's OS-provided root (e.g. /tmp on macOS).
        self.root = Path(self.temp.name).resolve()
        self.workshop = self.root / "warsztat-ąćęłńóśźż"
        self.workshop.mkdir()
        self.work = self.workshop / "work"
        self.work.mkdir()
        self.source = self.root / "CryptCustodian.exe"
        self.data = bytes(fixture())
        self.source.write_bytes(self.data)
        self.digest = hashlib.sha256(self.data).hexdigest()
        self.patch = mock.patch.object(inspector, "WORKSHOP", self.workshop)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def cli(self, *, source=None, digest=None, output="work/report.json"):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = inspector.main([
                "--source", str(self.source if source is None else source),
                "--expected-sha256", self.digest if digest is None else digest,
                "--output", output,
            ])
        return result, stdout.getvalue(), stderr.getvalue()

    def test_snapshot_rb_hash_and_no_writes(self):
        before = self.source.stat()
        real_open = open
        modes = []

        def tracked(path, mode, **kwargs):
            modes.append(mode)
            return real_open(path, mode, **kwargs)

        with mock.patch("builtins.open", side_effect=tracked):
            data, digest = inspector.read_snapshot(self.source, self.digest.upper())
        self.assertEqual(modes, ["rb"])
        self.assertEqual((data, digest), (self.data, self.digest))
        self.assertEqual(self.source.read_bytes(), self.data)
        self.assertEqual(self.source.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_cli_creates_only_new_report_and_does_not_modify_source(self):
        before = self.source.stat()
        self.assertEqual(self.cli()[0], 0)
        self.assertEqual([p.name for p in self.work.iterdir()], ["report.json"])
        report = json.loads((self.work / "report.json").read_bytes())
        self.assertEqual(report["source"]["sha256"], self.digest)
        self.assertEqual(report["xref_count"], 2)
        self.assertEqual(self.source.read_bytes(), self.data)
        self.assertEqual(self.source.stat().st_mtime_ns, before.st_mtime_ns)

    def test_wrong_or_malformed_hash_and_invalid_pe_publish_nothing(self):
        for digest in ("0" * 64, "", "x" * 64, self.digest + " "):
            with self.subTest(digest=digest):
                self.assertEqual(self.cli(digest=digest)[0], 2)
                self.assertEqual(list(self.work.iterdir()), [])
        broken = b"not a PE file"
        self.source.write_bytes(broken)
        self.assertEqual(self.cli(digest=hashlib.sha256(broken).hexdigest())[0], 2)
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertEqual(self.source.read_bytes(), broken)

    def test_wrong_hash_never_reaches_parser(self):
        with mock.patch.object(inspector, "PEImage") as parser:
            self.assertEqual(self.cli(digest="0" * 64)[0], 2)
            parser.assert_not_called()
        self.assertEqual(list(self.work.iterdir()), [])

    def test_limits_publish_nothing(self):
        for name in ("MAX_SOURCE_BYTES", "MAX_LITERALS", "MAX_XREFS", "MAX_REPORT_BYTES"):
            with self.subTest(name=name), mock.patch.object(inspector, name, 1):
                self.assertEqual(self.cli()[0], 2)
                self.assertEqual(list(self.work.iterdir()), [])

    def test_source_absolute_name_and_traversal_required(self):
        wrong = self.root / "Other.exe"
        wrong.write_bytes(self.data)
        for source in (Path("CryptCustodian.exe"), wrong,
                       self.work / ".." / ".." / "CryptCustodian.exe", self.root):
            with self.subTest(source=source):
                self.assertEqual(self.cli(source=source)[0], 2)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_missing_required_cli_arguments(self):
        for args in ([], ["--source", str(self.source)],
                     ["--source", str(self.source), "--expected-sha256", self.digest]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    inspector.main(args)
                self.assertEqual(raised.exception.code, 2)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_output_confinement_devices_ads_and_missing_directory(self):
        outputs = (str(self.root / "outside.json"), str(self.source),
                   "report.json", "work/../outside.json", "work/sub/report.json",
                   "work/report.txt", "work/CON.json", "work/LPT1.json",
                   "work/report.json:stream", "work/trailing.json.", "work/report.json ")
        for output in outputs:
            with self.subTest(output=output):
                self.assertEqual(self.cli(output=output)[0], 2)
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertFalse((self.root / "outside.json").exists())
        self.assertEqual(self.source.read_bytes(), self.data)
        self.work.rmdir()
        self.assertEqual(self.cli()[0], 2)
        self.assertFalse(self.work.exists())

    def test_existing_file_or_directory_never_overwritten(self):
        destination = self.work / "report.json"
        destination.write_bytes(b"keep me")
        self.assertEqual(self.cli()[0], 2)
        self.assertEqual(destination.read_bytes(), b"keep me")
        directory = self.work / "directory.json"
        directory.mkdir()
        self.assertEqual(self.cli(output="work/directory.json")[0], 2)
        self.assertTrue(directory.is_dir())

    def test_exclusive_creation_blocks_file_created_after_validation(self):
        destination = self.work / "report.json"
        real_os_open = os.open

        def racing_os_open(path, flags, mode=0o777):
            if Path(path) == destination and flags & os.O_EXCL:
                fd = real_os_open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    os.write(fd, b"racing sentinel")
                finally:
                    os.close(fd)
            return real_os_open(path, flags, mode)

        with mock.patch.object(inspector.os, "open", side_effect=racing_os_open):
            self.assertEqual(self.cli()[0], 2)
        self.assertEqual(destination.read_bytes(), b"racing sentinel")

    def test_source_changed_during_read_is_rejected(self):
        real_read = inspector._read_chunks

        def changed(stream, size):
            data = real_read(stream, size)
            with self.source.open("ab") as writer:
                writer.write(b"changed")
            return data

        with mock.patch.object(inspector, "_read_chunks", side_effect=changed):
            self.assertEqual(self.cli()[0], 2)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_source_same_size_mutation_even_if_timestamp_restored_fails_hash(self):
        original = self.source.stat()
        changed = bytearray(self.data)
        changed[0x700] ^= 1
        self.source.write_bytes(changed)
        os.utime(self.source, ns=(original.st_atime_ns, original.st_mtime_ns))
        self.assertEqual(self.cli()[0], 2)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_snapshot_growth_and_shrink_guards(self):
        for data, size in ((b"a", 2), (b"ab", 1)):
            with self.subTest(data=data), self.assertRaises(inspector.InspectorError):
                inspector._read_chunks(io.BytesIO(data), size)

    def test_reparse_attribute_rejected_without_symlink_privileges(self):
        info = types.SimpleNamespace(st_mode=stat.S_IFREG | 0o644,
                                     st_file_attributes=inspector.REPARSE_POINT)
        with self.assertRaises(inspector.InspectorError):
            inspector._not_link(cast(os.stat_result, info))

    def make_link(self, path, target, *, directory=False):
        try:
            path.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Symlink unavailable in synthetic sandbox: {error}")

    def test_source_symlink_rejected(self):
        directory = self.root / "links"
        directory.mkdir()
        link = directory / "CryptCustodian.exe"
        self.make_link(link, self.source)
        self.assertEqual(self.cli(source=link)[0], 2)
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertEqual(self.source.read_bytes(), self.data)

    def test_source_parent_symlink_rejected(self):
        target = self.root / "real-parent"
        target.mkdir()
        (target / "CryptCustodian.exe").write_bytes(self.data)
        link = self.root / "linked-parent"
        self.make_link(link, target, directory=True)
        self.assertEqual(self.cli(source=link / "CryptCustodian.exe")[0], 2)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_output_symlink_and_dangling_symlink_rejected(self):
        for name, target in (("existing.json", self.source),
                             ("dangling.json", self.root / "absent")):
            link = self.work / name
            self.make_link(link, target)
            self.assertEqual(self.cli(output=f"work/{name}")[0], 2)
            self.assertTrue(link.is_symlink())
        self.assertEqual(self.source.read_bytes(), self.data)
        self.assertFalse((self.root / "absent").exists())

    def test_output_work_symlink_rejected(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        self.work.rmdir()
        self.make_link(self.work, elsewhere, directory=True)
        self.assertEqual(self.cli()[0], 2)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_hardlink_output_cannot_overwrite_source(self):
        destination = self.work / "report.json"
        try:
            os.link(self.source, destination)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Hardlinks unavailable in synthetic sandbox: {error}")
        self.assertEqual(self.cli()[0], 2)
        self.assertEqual(self.source.read_bytes(), self.data)
        self.assertEqual(destination.read_bytes(), self.data)


if __name__ == "__main__":
    unittest.main()
