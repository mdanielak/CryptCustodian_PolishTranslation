"""Synthetic fixtures only. Never opens the installed game's data.win."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import font_inspector as fi


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fixture(version=(2022, 2, 0, 0), bytecode=17, lts=False,
            chars=fi.POLISH, float_em=False, include_lang=True, extra_strings=()):
    """Build independent UMT-style absolute-pointer records in memory."""
    data = bytearray(b"FORM\0\0\0\0")
    marks = {}

    def chunk(tag, payload, *, last=False):
        header = len(data)
        # UMT: padding belongs to this chunk; the following header is absolute
        # 16-byte aligned. The last chunk and legacy GMS1 need no padding.
        if not last and (version[0] >= 2 or (version[0] == 1 and version[3] >= 9999)):
            payload += b"\0" * (-(header + 8 + len(payload)) % 16)
        data.extend(tag.encode("ascii") + struct.pack("<I", len(payload)) + payload)
        marks[tag] = header + 8
        return header + 8

    gen = bytearray(0x3C)
    gen[0] = 1  # Debug flag is NOT the bytecode version.
    gen[1] = bytecode
    struct.pack_into("<4I", gen, 0x2C, *version)
    chunk("GEN8", gen)

    # STRG pointers address the length; FONT/LANG pointers address UTF-8 bytes.
    texts = ("fnt_zażółć", "Łódź ĄĆĘŁŃÓŚŹŻ", "ID_GREETING", "ID_EMPTY",
             "English", "en-US", "Hello", "", "Polish", "pl-PL",
             'Zażółć gęślą jaźń ĄĆĘŁŃÓŚŹŻ 😀\n"tekst"\\',
             "TRANSLATIONS.INI Translations2.Ini INI_OPEN Ini_Read_String LANGUAGE FONT",
             "UNREFERENCED_CORPUS_SENTINEL", *extra_strings)
    strings = bytearray(struct.pack("<I", len(texts)) + b"\0" * (4 * len(texts)))
    string_base = len(data) + 8
    string_ptrs = []
    for i, text in enumerate(texts):
        raw = text.encode("utf-8")
        address = string_base + len(strings)
        struct.pack_into("<I", strings, 4 + i * 4, address)
        string_ptrs.append(address + 4)
        strings.extend(struct.pack("<i", len(raw)) + raw + b"\0")
    chunk("STRG", strings)
    marks["string"] = string_ptrs[0]
    marks["strings"] = string_ptrs

    if include_lang:
        # unknown1, L, E, E IDs, then L inline (name, region, E translations).
        lang = struct.pack("<3I", 7, 2, 2)
        lang += struct.pack("<2I", string_ptrs[2], string_ptrs[3])
        lang += struct.pack("<4I", *(string_ptrs[i] for i in (4, 5, 6, 7)))
        lang += struct.pack("<4I", *(string_ptrs[i] for i in (8, 9, 10, 7)))
        chunk("LANG", lang)

    tpag_base = len(data) + 8
    texture_ptr = tpag_base + 8
    tpag = struct.pack("<II10Hh", 1, texture_ptr,
                       3, 4, 128, 64, 0, 0, 128, 64, 128, 64, -1)
    chunk("TPAG", tpag)
    marks["texture"] = texture_ptr

    font_base = len(data) + 8
    font_ptr = font_base + 8
    font = bytearray(struct.pack("<II", 1, font_ptr))
    em = struct.unpack("<I", struct.pack("<f", -18.5))[0] if float_em else 18
    font.extend(struct.pack("<5IHBBIIff", *string_ptrs[:2], em, 1, 0,
                            0, 238, 1, 65535, texture_ptr, 1.0, 1.25))
    marks["font"] = font_ptr
    if bytecode >= 17:
        font.extend(struct.pack("<i", -3))
    if version >= (2022, 2, 0, 0):
        font.extend(struct.pack("<I", 20))
    if version >= (2023, 2, 0, 0) and not lts:
        font.extend(struct.pack("<I", 2))
    if version >= (2023, 6, 0, 0):
        font.extend(struct.pack("<I", 24))
    list_relative = len(font)
    marks["glyph_list"] = font_base + list_relative
    font.extend(struct.pack("<I", len(chars)) + b"\0" * (4 * len(chars)))
    glyph_ptrs = []
    for i, char in enumerate(chars):
        address = font_base + len(font)
        glyph_ptrs.append(address)
        struct.pack_into("<I", font, list_relative + 4 + i * 4, address)
        font.extend(struct.pack("<5H2h", ord(char), i, 2, 6, 10, -2, -1))
        if version >= (2024, 11, 0, 0):
            font.extend(struct.pack("<h", 0))
        font.extend(struct.pack("<H", 1 if i == 0 else 0))
        if i == 0:
            font.extend(struct.pack("<hh", -1, -4))
    marks["glyphs"] = glyph_ptrs
    # FONT can have trailing data (e.g. a character map); do not infer glyphs from it.
    font.extend(b"\0" * 512)
    chunk("FONT", font, last=True)
    struct.pack_into("<I", data, 4, len(data) - 8)
    return bytes(data), marks


def altered(data, fmt, offset, *values):
    raw = bytearray(data)
    struct.pack_into("<" + fmt, raw, offset, *values)
    return bytes(raw)


def lang_padding_fixture(padding=b"\0" * 12, *, entries=0, last=False):
    """Synthetic LANG at the reported real offsets; no installed data is read.

    Add a valid STRG filler string to place LANG header at 0xA20, keeping the
    preceding chunk's padding minimal and all absolute pointers valid.
    Explicit padding allows malformed LANG tails without damaging the next header.
    """
    header = 0xA20
    _, marks = fixture((2, 0, 0, 0), extra_strings=("",))
    gap = header - (marks["LANG"] - 8)
    assert gap >= 0 and gap % 16 == 0
    data, marks = fixture((2, 0, 0, 0), extra_strings=("x" * gap,))
    assert marks["LANG"] - 8 == header
    prefix = data[:header]
    model = struct.pack("<3I", 7, 0, entries)
    model += struct.pack(f"<{entries}I", *marks["strings"][2:2 + entries])
    payload = model + padding
    data = prefix + b"LANG" + struct.pack("<I", len(payload)) + payload
    if not last:
        data += b"EXTN" + struct.pack("<II", 4, 0)  # Empty list; last chunk.
    return altered(data, "I", 4, len(data) - 8)


class ParserTests(unittest.TestCase):
    def parse(self, data, **options):
        return fi.inspect_bytes(data, digest(data), **options)

    def refuses(self, data, **options):
        with self.assertRaises(fi.InspectorError):
            self.parse(data, **options)

    def test_prefix_absolute_links_and_polish_utf8(self):
        data, marks = fixture()
        report = self.parse(data)
        font = report["fonts"][0]
        self.assertEqual(report["gen8"]["bytecode"], 17)
        self.assertEqual(report["gen8"]["major"], 2022)
        self.assertEqual(font["name"], "fnt_zażółć")
        self.assertEqual(font["display_name"], "Łódź ĄĆĘŁŃÓŚŹŻ")
        self.assertEqual(font["glyph_list_offset"], marks["font"] + 0x30)
        self.assertTrue(font["bold"])
        self.assertFalse(font["italic"])
        self.assertEqual(font["charset"], 238)
        self.assertEqual(font["scale_y"], 1.25)
        self.assertEqual(font["texture_pointer"], marks["texture"])
        self.assertEqual(font["tpag_index"], 0)
        self.assertEqual(report["tpag"][0]["texture_page_id"], -1)
        self.assertEqual(font["glyph_count"], 18)
        self.assertTrue(font["polish"]["complete"])
        self.assertTrue(report["polish_complete_in_every_font"])
        self.assertEqual(font["glyphs"][0]["shift"], -2)
        self.assertEqual(font["glyphs"][0]["bearing_offset"], -1)
        self.assertEqual(font["glyphs"][0]["kerning"], [[-1, -4]])

    def test_version_boundaries_and_offsets(self):
        cases = [((1, 4, 0, 0), 16, False, 0x28),
                 ((2, 3, 0, 0), 17, False, 0x2C),
                 ((2022, 1, 0, 0), 17, False, 0x2C),
                 ((2022, 2, 0, 0), 17, False, 0x30),
                 ((2023, 1, 0, 0), 17, False, 0x30),
                 ((2023, 2, 0, 0), 17, False, 0x34),
                 ((2023, 2, 0, 0), 17, True, 0x30),
                 ((2023, 6, 0, 0), 17, False, 0x38),
                 ((2023, 6, 0, 0), 17, True, 0x34),
                 ((2024, 11, 0, 0), 17, False, 0x38)]
        for version, bytecode, lts, offset in cases:
            with self.subTest(version=version, lts=lts):
                data, marks = fixture(version, bytecode, lts)
                options = {"lts": lts, "lts_evidence": "synthetic fixture"}
                font = self.parse(data, **options)["fonts"][0]
                self.assertEqual(font["glyph_list_offset"], marks["font"] + offset)
                self.assertEqual(font["glyphs"][0]["kerning"], [[-1, -4]])

    def test_lts_requires_explicit_evidence_for_full_parse(self):
        data, _ = fixture((2023, 2, 0, 0))
        report = self.parse(data)
        self.assertEqual(report["status"], "partial")
        self.assertFalse(report["font_parse_performed"])
        self.assertIn("LTS", report["blocker"])
        self.assertEqual(report["lang"]["language_count"], 2)
        self.assertEqual(report["strg_hits"]["matched_count"], 3)
        self.assertNotIn("fonts", report)
        self.assertNotIn("polish_complete_in_every_font", report)
        self.refuses(data, lts=False)
        self.refuses(data, lts_evidence="synthetic")
        self.refuses(data, lts=False, lts_evidence=" ")

    def test_ambiguous_gen8_returns_partial_report_without_version_evidence(self):
        data, marks = fixture()
        data = altered(data, "4I", marks["GEN8"] + 0x2C, 2, 0, 0, 0)
        report = self.parse(data)
        self.assertEqual(report["status"], "partial")
        self.assertFalse(report["font_parse_performed"])
        self.assertIn("--format-version", report["blocker"])
        self.assertEqual(report["gen8"]["major"], 2)
        self.assertNotIn("fonts", report)
        self.assertNotIn("polish_complete_in_every_font", report)
        self.refuses(data, format_version="2022.2.0.0")
        report = self.parse(data, format_version="2022.2.0.0",
                            version_evidence="synthetic known layout")
        self.assertEqual(report["gen8"]["major"], 2)
        self.assertEqual(report["gen8"]["effective_layout_version"], [2022, 2, 0, 0])

    def test_unsupported_versions_and_bytecode(self):
        for version, bytecode in [((2024, 14, 0, 0), 17), ((2025, 1, 0, 0), 17),
                                  ((3, 0, 0, 0), 17), ((2022, 2, 0, 0), 18)]:
            with self.subTest(version=version, bytecode=bytecode):
                data, _ = fixture(version, bytecode)
                report = self.parse(data, lts=False, lts_evidence="synthetic")
                self.assertEqual(report["status"], "partial")
                self.assertFalse(report["font_parse_performed"])

                self.assertEqual(report["lang"]["entry_count"], 2)
                self.assertIn("strg_summary", report)
                self.assertIn("strg_hits", report)

    def test_confirmed_legacy_2_0_and_invalid_version_syntax(self):
        data, _ = fixture((2, 0, 0, 0))
        report = self.parse(data, format_version="2.0.0.0",
                            version_evidence="synthetic confirmed legacy layout")
        self.assertEqual(report["fonts"][0]["glyph_count"], 18)
        for value in ("2.0", "2023.2.0.-1", "4294967296.0.0.0", "2.x.0.0"):
            with self.subTest(value=value):
                self.refuses(data, format_version=value, version_evidence="synthetic")

    def test_float_em_size_sign_bit(self):
        data, _ = fixture(float_em=True)
        font = self.parse(data)["fonts"][0]
        self.assertEqual(font["em_size"], 18.5)
        self.assertTrue(font["em_size_is_float"])

    def test_partial_lang_unicode_and_full_parser_share_string_contract(self):
        data, marks = fixture()
        full = self.parse(data)
        partial_data = altered(data, "4I", marks["GEN8"] + 0x2C, 2, 0, 0, 0)
        # Deliberately unusable FONT/TPAG: partial must not parse either payload.
        partial_data = altered(partial_data, "I", marks["FONT"], 0xFFFFFFFF)
        partial_data = altered(partial_data, "I", marks["TPAG"], 0xFFFFFFFF)
        before = digest(partial_data)
        report = self.parse(partial_data)
        self.assertEqual(report["status"], "partial")
        self.assertFalse(report["font_parse_performed"])
        self.assertNotIn("fonts", report)
        self.assertNotIn("tpag", report)
        for key in ("strg_summary", "strg_hits", "lang"):
            self.assertEqual(report[key], full[key])
        lang = report["lang"]
        self.assertEqual((lang["unknown1"], lang["language_count"], lang["entry_count"]),
                         (7, 2, 2))
        self.assertEqual(lang["entry_ids"], ["ID_GREETING", "ID_EMPTY"])
        self.assertEqual(lang["entry_id_pointers"], marks["strings"][2:4])
        self.assertEqual([(row["name"], row["region"]) for row in lang["languages"]],
                         [("English", "en-US"), ("Polish", "pl-PL")])
        self.assertEqual(lang["languages"][0]["translations"], ["Hello", ""])
        self.assertEqual(lang["languages"][1]["translations"],
                         ['Zażółć gęślą jaźń ĄĆĘŁŃÓŚŹŻ 😀\n"tekst"\\', ""])
        self.assertEqual(lang["languages"][1]["translation_pointers"],
                         [marks["strings"][10], marks["strings"][7]])
        encoded = json.dumps(report, ensure_ascii=True, allow_nan=False)
        self.assertTrue(encoded.isascii())
        self.assertEqual(json.loads(encoded), report)
        self.assertEqual(digest(partial_data), before)
        self.assertEqual(report["source"]["sha256"], before)
        with self.assertRaisesRegex(fi.InspectorError, "SHA-256"):
            fi.inspect_bytes(partial_data, "0" * 64)
        self.assertIn(fi.STRING_WARNING, report["warnings"])
        self.assertIn(fi.STRING_WARNING, full["warnings"])

    def test_lang_absent_and_strg_required_in_partial_and_full(self):
        for version in ((2, 0, 0, 0), (2022, 2, 0, 0)):
            with self.subTest(version=version):
                data, marks = fixture(version, include_lang=False)
                report = self.parse(data)
                self.assertIsNone(report["lang"])
                self.assertIn("strg_summary", report)
                self.assertIn("strg_hits", report)
                with self.assertRaisesRegex(fi.InspectorError, "Brak wymaganego chunku STRG"):
                    self.parse(altered(data, "4s", marks["STRG"] - 8, b"TEST"))

    def test_partial_needs_no_font_or_tpag_chunks(self):
        data, marks = fixture((2, 0, 0, 0))
        for name, replacement in (("FONT", b"TES1"), ("TPAG", b"TES2")):
            data = altered(data, "4s", marks[name] - 8, replacement)
        self.assertEqual(self.parse(data)["lang"]["language_count"], 2)

    def test_strg_hits_case_insensitive_bounded_and_no_corpus_dump(self):
        long_text = "FoNt " + "ą" * (fi.MAX_HIT_CHARS + 10)
        data, marks = fixture((2, 0, 0, 0), extra_strings=(long_text,))
        report = self.parse(data)
        summary, hits = report["strg_summary"], report["strg_hits"]
        self.assertEqual(summary["count"], len(marks["strings"]))
        self.assertEqual(summary["validated_count"], summary["count"])
        self.assertEqual(summary["total_string_bytes"], sum(
            struct.unpack_from("<i", data, p - 4)[0] for p in marks["strings"]))
        self.assertEqual(hits["matched_count"], 4)
        self.assertEqual(hits["omitted_count"], 0)
        self.assertEqual([item["index"] for item in hits["items"]], [4, 8, 11, 13])
        self.assertTrue(all(hits["term_counts"][term] >= 1 for term in fi.STRG_TERMS))
        item = hits["items"][-1]
        self.assertEqual(item["terms"], ["font"])
        self.assertEqual(item["content_pointer"], marks["strings"][-1])
        self.assertEqual(item["object_pointer"], marks["strings"][-1] - 4)
        self.assertEqual(item["preview"], long_text[:fi.MAX_HIT_CHARS])
        self.assertTrue(item["preview_truncated"])
        self.assertNotIn("UNREFERENCED_CORPUS_SENTINEL", json.dumps(report))
        self.assertNotIn("fnt_zażółć", json.dumps(report, ensure_ascii=False))
        with patch.object(fi, "MAX_STRG_HITS", 1):
            bounded = self.parse(data)["strg_hits"]
        self.assertEqual(bounded["reported_count"], 1)
        self.assertEqual(bounded["matched_count"], 4)
        self.assertEqual(bounded["omitted_count"], 3)
        self.assertEqual(bounded["term_counts"], hits["term_counts"])

    def test_strg_serialized_order_and_lang_links_do_not_depend_on_it(self):
        data, marks = fixture((2, 0, 0, 0))
        before = self.parse(data)
        reversed_objects = [p - 4 for p in reversed(marks["strings"])]
        data = altered(data, f"{len(reversed_objects)}I", marks["STRG"] + 4,
                       *reversed_objects)
        report = self.parse(data)
        self.assertEqual(report["lang"], before["lang"])
        self.assertEqual(report["strg_summary"], before["strg_summary"])
        self.assertEqual([item["content_pointer"] for item in report["strg_hits"]["items"]],
                         [marks["strings"][i] for i in (11, 8, 4)])
        self.assertEqual([item["index"] for item in report["strg_hits"]["items"]], [1, 4, 8])

    def test_partial_strg_validates_unreferenced_strings_after_hit_limit(self):
        data, marks = fixture((2, 0, 0, 0))
        sp = marks["strings"][-1]
        size = struct.unpack_from("<i", data, sp - 4)[0]
        variants = [("i", sp - 4, -1), ("i", sp - 4, fi.MAX_STRING_BYTES + 1),
                    ("i", sp - 4, size + 1), ("B", sp, 0xFF), ("B", sp, 0),
                    ("B", sp + size, 65), ("I", marks["STRG"], 0xFFFFFFFF),
                    ("I", marks["STRG"] + 4, 0),
                    ("I", marks["STRG"] + 4, marks["STRG"]),
                    ("I", marks["STRG"] + 4, marks["LANG"]),
                    ("I", marks["STRG"] + 4, marks["strings"][0]),
                    ("I", marks["STRG"] + 8, marks["strings"][0] - 4)]
        with patch.object(fi, "MAX_STRG_HITS", 0):
            for fmt, where, value in variants:
                with self.subTest(where=where, value=value):
                    self.refuses(altered(data, fmt, where, value))

    def test_lang_bad_counts_and_pointer_ownership_fail_even_in_partial(self):
        for version in ((2, 0, 0, 0), (2022, 2, 0, 0)):
            data, marks = fixture(version)
            start = marks["LANG"]
            for where, value in ((start + 4, 0xFFFFFFFF), (start + 8, 0xFFFFFFFF),
                                 (start + 4, 3), (start + 8, 3),
                                 (start + 4, 1), (start + 8, 1)):
                with self.subTest(version=version, where=where, value=value):
                    with self.assertRaisesRegex(fi.InspectorError, "LANG"):
                        self.parse(altered(data, "I", where, value))
            # Every kind of LANG field: entry ID, name, region, translation.
            for where in (start + 12, start + 20, start + 24, start + 28):
                for pointer in (0, len(data) + 4, marks["STRG"], start + 12,
                                marks["strings"][0] - 4, marks["strings"][0] + 1,
                                marks["texture"]):
                    with self.subTest(version=version, where=where, pointer=pointer):
                        with self.assertRaisesRegex(fi.InspectorError, "STRG"):
                            self.parse(altered(data, "I", where, pointer))

    def test_lang_truncated_header_and_empty_tables(self):
        data, marks = fixture((2, 0, 0, 0))
        start = marks["LANG"]
        # End FORM at LANG; FONT/TPAG aren't required by partial.
        for size in (0, 4, 8, 11):
            short = altered(data[:start + size], "I", start - 4, size)
            short = altered(short, "I", 4, len(short) - 8)
            with self.subTest(size=size), self.assertRaisesRegex(fi.InspectorError, "LANG"):
                self.parse(short)
        for languages, entries, pointers in ((0, 0, ()), (0, 2, (2, 3)),
                                             (1, 0, (4, 5))):
            payload = struct.pack("<3I", 7, languages, entries)
            payload += struct.pack(f"<{len(pointers)}I",
                                   *(marks["strings"][i] for i in pointers))
            empty = data[:start] + payload
            empty = altered(empty, "I", start - 4, len(payload))
            empty = altered(empty, "I", 4, len(empty) - 8)
            with self.subTest(languages=languages, entries=entries):
                lang = self.parse(empty)["lang"]
                self.assertEqual(lang["language_count"], languages)
                self.assertEqual(lang["entry_count"], entries)
                self.assertEqual(len(lang["entry_ids"]), entries)
                self.assertEqual(len(lang["languages"]), languages)
                self.assertEqual(lang["model_size"], len(payload))
                self.assertEqual(lang["padding_size"], 0)
                self.assertEqual(lang["padding_alignment"], 16)

    def test_fixture_chunk_alignment_and_owned_padding(self):
        data, marks = fixture()
        chunks = self.parse(data)["chunks"]
        self.assertEqual(chunks[0]["header_offset"], 8)  # First child of FORM.
        for previous, current in zip(chunks, chunks[1:]):
            self.assertEqual(current["header_offset"] % 16, 0)
            self.assertEqual(previous["payload_offset"] + previous["size"],
                             current["header_offset"])
        lang = self.parse(data)["lang"]
        self.assertEqual(lang["model_size"], 52)
        self.assertEqual(lang["padding_size"], 4)
        self.assertEqual(lang["padding_alignment"], 16)
        self.assertEqual(data[marks["LANG"] + 52:marks["TPAG"] - 8], b"\0" * 4)

    def test_lang_empty_twelve_zero_padding_at_real_offsets(self):
        report = self.parse(lang_padding_fixture())
        self.assertEqual(report["status"], "partial")
        lang = report["lang"]
        self.assertEqual(lang["offset"], 0xA28)
        self.assertEqual((lang["language_count"], lang["entry_count"]), (0, 0))
        self.assertEqual((lang["model_size"], lang["padding_size"],
                          lang["padding_alignment"]), (12, 12, 16))
        self.assertEqual(report["chunks"][-2],
                         {"name": "LANG", "header_offset": 0xA20,
                          "payload_offset": 0xA28, "size": 24})
        self.assertEqual(report["chunks"][-1]["name"], "EXTN")
        self.assertEqual(report["chunks"][-1]["header_offset"], 0xA40)
        self.assertEqual(json.loads(json.dumps(report, ensure_ascii=True)), report)

    def test_lang_no_padding_when_model_aligned_or_last(self):
        # Three IDs, no languages: model 24 ends at 0xA40, no padding needed.
        # Last LANG: exact model 12 may end unaligned at 0xA34.
        for entries, last, model_size in ((3, False, 24), (0, True, 12)):
            with self.subTest(entries=entries, last=last):
                report = self.parse(lang_padding_fixture(b"", entries=entries, last=last))
                lang = report["lang"]
                self.assertEqual(lang["model_size"], model_size)
                self.assertEqual(lang["padding_size"], 0)
                self.assertEqual(lang["padding_alignment"], 16)
                if not last:
                    self.assertEqual(report["chunks"][-1]["header_offset"], 0xA40)

    def test_lang_nonzero_padding_rejected_at_every_position(self):
        for index in range(12):
            padding = bytearray(12)
            padding[index] = 1
            with self.subTest(index=index), self.assertRaisesRegex(fi.InspectorError,
                                                                  "LANG.*niezerowy"):
                self.parse(lang_padding_fixture(bytes(padding)))

    def test_lang_wrong_padding_length_excess_and_unaligned_end(self):
        # 4/8-byte heuristics, short/long tails, >15 zeros and an extra full
        # alignment block (28 keeps EXTN aligned!) must all be rejected.
        for size in (1, 3, 4, 8, 11, 13, 15, 16, 28):
            data = lang_padding_fixture(b"\0" * size)
            self.assertEqual(data[0xA34 + size:0xA3C + size],
                             b"EXTN" + struct.pack("<I", 4))
            with self.subTest(size=size), self.assertRaisesRegex(fi.InspectorError, "LANG"):
                self.parse(data)
        # Relative model alignment would accept 4 bytes here; absolute needs 12.
        self.assertEqual((12 + 4) % 16, 0)
        self.assertNotEqual((0xA28 + 12 + 4) % 16, 0)
        # Already aligned model: even another aligned block is excess.
        with self.assertRaisesRegex(fi.InspectorError, "LANG"):
            self.parse(lang_padding_fixture(b"\0" * 16, entries=3))

    def test_lang_padding_requires_registered_valid_successor(self):
        with self.assertRaisesRegex(fi.InspectorError, "LANG"):
            self.parse(lang_padding_fixture(last=True))
        data = lang_padding_fixture()
        # No scanning past garbage, truncated/overflowing headers or duplicates.
        variants = [altered(data, "4s", 0xA40, b"bad!"),
                    altered(data, "I", 0xA44, 0xFFFFFFFF),
                    altered(data, "4s", 0xA40, b"STRG"),
                    altered(data[:0xA44], "I", 4, 0xA44 - 8)]
        for bad in variants:
            with self.subTest(sha256=digest(bad)):
                self.refuses(bad)
        # A plausible header in bytes is not enough without FORM registration.
        reader = fi.Reader(data)
        region = fi.Region(0xA28, 0xA40, "LANG")
        strg = next(chunk for chunk in self.parse(data)["chunks"] if chunk["name"] == "STRG")
        strings = fi.StringTable(reader, fi.Region(strg["payload_offset"],
                                                  strg["payload_offset"] + strg["size"], "STRG"))
        for chunks in ([], [{"header_offset": 0xA50}]):
            with self.subTest(chunks=chunks), self.assertRaisesRegex(fi.InspectorError, "LANG"):
                fi.parse_lang(reader, region, strings, chunks)

    def test_lang_semantic_limits_including_repeated_text_occurrences(self):
        data, _ = fixture((2, 0, 0, 0))
        lang = self.parse(data)["lang"]
        texts = list(lang["entry_ids"])
        for row in lang["languages"]:
            texts.extend([row["name"], row["region"], *row["translations"]])
        budget = sum(len(json.dumps(text, ensure_ascii=True)) for text in texts)
        with patch.object(fi, "MAX_LANG_TEXT_JSON", budget):
            self.assertEqual(self.parse(data)["lang"], lang)
        for name, value in (("MAX_LANGUAGES", 1), ("MAX_LANG_ENTRIES", 1),
                            ("MAX_LANG_TRANSLATIONS", 3), ("MAX_LANG_TEXT_JSON", budget - 1)):
            with self.subTest(name=name), patch.object(fi, name, value):
                with self.assertRaisesRegex(fi.InspectorError, "LANG.*limit"):
                    self.parse(data)

    def test_missing_letters_ranges_and_no_inference_from_declared_range(self):
        data, _ = fixture(chars="ABCą")
        font = self.parse(data)["fonts"][0]
        self.assertEqual(font["glyph_ranges"], [[65, 67], [261, 261]])
        self.assertEqual(font["codepoints"], [65, 66, 67, 261])
        self.assertEqual(font["polish"]["present"], ["ą"])
        self.assertFalse(font["polish"]["complete"])
        self.assertIn("Ą", font["polish"]["missing"])

    def test_empty_fonts_not_vacuously_complete(self):
        data, marks = fixture()
        data = altered(data, "I", marks["FONT"], 0)
        report = self.parse(data)
        self.assertEqual(report["font_count"], 0)
        self.assertFalse(report["polish_complete_in_every_font"])

    def test_empty_glyphs_null_texture(self):
        data, marks = fixture(chars="")
        data = altered(data, "I", marks["font"] + 0x1C, 0)
        font = self.parse(data)["fonts"][0]
        self.assertIsNone(font["tpag_index"])
        self.assertEqual(font["glyph_count"], 0)
        self.assertFalse(font["polish"]["complete"])

    def test_hash_and_hash_syntax(self):
        data, _ = fixture()
        for expected in ("", "z" * 64, "0" * 64):
            with self.subTest(expected=expected), self.assertRaises(fi.InspectorError):
                fi.inspect_bytes(data, expected)
        self.assertEqual(fi.inspect_bytes(data, digest(data).upper())["status"], "ok")

    def test_truncated_form_and_trailing_bytes(self):
        data, _ = fixture()
        for bad in (b"", b"FORM", data[:-1], data + b"\0",
                    b"NOPE" + data[4:], altered(data, "I", 4, 0)):
            with self.subTest(length=len(bad)):
                self.refuses(bad)

    def test_chunk_overflow_duplicate_missing_and_short_gen8(self):
        data, marks = fixture()
        variants = [altered(data, "I", marks["GEN8"] - 4, 0xFFFFFFFF),
                    altered(data, "4s", marks["FONT"] - 8, b"TPAG"),
                    altered(data, "4s", marks["GEN8"] - 8, b"TEST"),
                    altered(data, "4s", marks["FONT"] - 8, b"bad!"),
                    altered(data, "I", marks["GEN8"] - 4, 4)]
        for bad in variants:
            self.refuses(bad)

    def test_count_limits_and_truncated_tables(self):
        data, marks = fixture()
        for where in (marks["FONT"], marks["TPAG"], marks["STRG"], marks["glyph_list"]):
            with self.subTest(where=where):
                self.refuses(altered(data, "I", where, 0xFFFFFFFF))
        # Below MAX_GLYPHS, but table does not fit the owning FONT record.
        self.refuses(altered(data, "I", marks["glyph_list"], 60000))

    def test_pointer_ownership_null_table_and_record_middle(self):
        data, marks = fixture()
        cases = [(marks["FONT"] + 4, 0),
                 (marks["FONT"] + 4, marks["FONT"]),
                 (marks["FONT"] + 4, marks["STRG"]),
                 (marks["glyph_list"] + 4, marks["font"]),
                 (marks["glyph_list"] + 4, marks["texture"]),
                 (marks["TPAG"] + 4, len(data) + 1),
                 (marks["font"] + 0x1C, marks["texture"] + 1),
                 (marks["font"], marks["string"] - 4)]
        for where, value in cases:
            with self.subTest(where=where, value=value):
                self.refuses(altered(data, "I", where, value))

    def test_duplicate_and_overlapping_glyph_pointers(self):
        data, marks = fixture()
        gp = marks["glyphs"][0]
        for value in (gp, gp + 2):
            self.refuses(altered(data, "I", marks["glyph_list"] + 8, value))

    def test_unsorted_pointer_list_keeps_serialized_order(self):
        data, marks = fixture(chars="Aą")
        first, second = marks["glyphs"]
        data = altered(data, "II", marks["glyph_list"] + 4, second, first)
        font = self.parse(data)["fonts"][0]
        self.assertEqual([g["character"] for g in font["glyphs"]], ["ą", "A"])
        self.assertEqual(font["codepoints"], [65, 261])

    def test_tpag_record_requires_all_22_bytes(self):
        data, marks = fixture()
        # Include chunk padding when placing the record so only 21 bytes remain.
        short_pointer = marks["FONT"] - 8 - 21
        self.refuses(altered(data, "I", marks["TPAG"] + 4, short_pointer))
        self.refuses(altered(data, "h", marks["texture"] + 20, -2))

    def test_glyph_codes_bool_nonfinite_and_bad_range(self):
        data, marks = fixture()
        variants = [("H", marks["glyphs"][1], ord(fi.POLISH[0])),
                    ("H", marks["glyphs"][0], 0xD800),
                    ("I", marks["font"] + 0x0C, 2),
                    ("I", marks["font"] + 0x10, 256),
                    ("f", marks["font"] + 0x20, float("nan")),
                    ("f", marks["font"] + 0x24, float("inf")),
                    ("I", marks["font"] + 0x18, 0xFFFFFFFF)]
        for fmt, where, value in variants:
            self.refuses(altered(data, fmt, where, value))

    def test_string_length_utf8_nul_and_membership(self):
        data, marks = fixture()
        sp = marks["string"]
        size = struct.unpack_from("<i", data, sp - 4)[0]
        for bad in (altered(data, "i", sp - 4, -1),
                    altered(data, "i", sp - 4, fi.MAX_STRING_BYTES + 1),
                    altered(data, "i", sp - 4, size + 4),
                    altered(data, "B", sp, 0xFF),
                    altered(data, "B", sp, 0),
                    altered(data, "B", sp + size, 65),
                    altered(data, "I", marks["font"], sp + 1)):
            self.refuses(bad)

    def test_2024_extra_zero_and_kerning_bounds(self):
        data, marks = fixture((2024, 11, 0, 0))
        options = {"lts": False, "lts_evidence": "synthetic"}
        gp = marks["glyphs"][0]
        self.refuses(altered(data, "h", gp + 14, 1), **options)
        self.refuses(altered(data, "H", gp + 16, 65535), **options)
        self.assertEqual(self.parse(data, **options)["fonts"][0]["glyphs"][0]
                         ["unknown_always_zero"], 0)

    def test_global_budgets(self):
        data, _ = fixture()
        for setting, value in (("MAX_TOTAL_GLYPHS", 1), ("MAX_TOTAL_KERNING", 0),
                               ("MAX_CHUNKS", 1), ("MAX_SOURCE", 1)):
            with self.subTest(setting=setting), patch.object(fi, setting, value):
                self.refuses(data)

    def test_json_round_trip_and_source_bytes_unchanged(self):
        data, _ = fixture()
        before = digest(data)
        report = self.parse(data)
        encoded = json.dumps(report, ensure_ascii=True, allow_nan=False)
        self.assertTrue(encoded.isascii())
        self.assertEqual(json.loads(encoded), report)
        self.assertEqual(digest(data), before)


class FilesystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-font-inspector-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.work = self.root / "work"
        self.work.mkdir()
        self.source = self.root / "data.win"
        self.data, _ = fixture()
        self.source.write_bytes(self.data)  # Synthetic bytes, not a game copy.
        for name, value in (("ROOT", self.root), ("WORK", self.work)):
            patcher = patch.object(fi, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def cli(self, *extra):
        args = ["inspect", "--source", str(self.source), "--expected-sha256",
                digest(self.data), *extra]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = fi.main(args)
        return code, out.getvalue(), err.getvalue()

    def test_stdout_only_does_not_create_report(self):
        code, out, err = self.cli()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["status"], "ok")
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertEqual(self.source.read_bytes(), self.data)

    def test_new_json_only_and_no_overwrite(self):
        code, out, err = self.cli("--output", "work/report.json")
        self.assertEqual((code, out, err), (0, "", ""))
        target = self.work / "report.json"
        original = target.read_bytes()
        self.assertEqual(json.loads(original)["font_count"], 1)
        self.assertEqual(self.cli("--output", str(target))[0], 2)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(self.source.read_bytes(), self.data)

    def test_partial_lang_cli_json_and_unchanged_synthetic_source(self):
        self.data, _ = fixture((2, 0, 0, 0))
        self.source.write_bytes(self.data)
        code, out, err = self.cli()
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.isascii())
        report = json.loads(out)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["lang"]["languages"][1]["name"], "Polish")
        self.assertEqual(self.cli("--output", "work/partial.json"), (0, "", ""))
        self.assertEqual(json.loads((self.work / "partial.json").read_bytes()), report)
        self.assertEqual(self.source.read_bytes(), self.data)

    def test_partial_lang_failure_or_budget_never_publishes_report(self):
        data, marks = fixture((2, 0, 0, 0))
        variants = [altered(data, "I", marks["LANG"] + 4, 0xFFFFFFFF),
                    altered(data, "I", marks["LANG"] + 12, 0),
                    altered(data, "B", marks["LANG"] + 52, 1),
                    altered(data, "B", marks["strings"][-1], 0xFF)]
        for bad in variants:
            with self.subTest(sha256=digest(bad)):
                self.data = bad
                self.source.write_bytes(bad)
                code, out, err = self.cli("--output", "work/refused.json")
                self.assertEqual((code, out), (2, ""))
                self.assertIn("Odmowa inspekcji", err)
                self.assertEqual(list(self.work.iterdir()), [])
                self.assertEqual(self.source.read_bytes(), bad)
        self.data = data
        self.source.write_bytes(data)
        with patch.object(fi, "MAX_LANG_TEXT_JSON", 1):
            code, out, err = self.cli("--output", "work/refused.json")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("LANG", err)
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertEqual(self.source.read_bytes(), data)

    def test_output_escape_extension_reserved_and_missing_parent(self):
        for value in (str(self.root / "outside.json"), "work/../outside.json",
                      "work/file.txt", "work/missing/report.json", "work/NUL.json",
                      "work/report.json:stream", str(self.source)):
            with self.subTest(value=value):
                self.assertEqual(self.cli("--output", value)[0], 2)
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertEqual(self.source.read_bytes(), self.data)

    def test_bad_hash_does_not_create_output(self):
        self.source.write_bytes(self.data + b"bad")
        self.assertEqual(self.cli("--output", "work/refused.json")[0], 2)
        self.assertFalse((self.work / "refused.json").exists())

    def test_source_absolute_and_filename_required(self):
        for value in (Path("data.win"), self.root / "another.win"):
            with self.assertRaises(fi.InspectorError):
                fi.read_source(value)

    def test_report_budget_no_file_created(self):
        with patch.object(fi, "MAX_REPORT", 1):
            self.assertEqual(self.cli("--output", "work/large.json")[0], 2)
        self.assertFalse((self.work / "large.json").exists())

    def test_output_symlink_refused_when_supported(self):
        outside = self.root / "outside"
        outside.mkdir()
        link = self.work / "link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"Brak uprawnień/obsługi symlinków: {exc}")
        self.assertEqual(self.cli("--output", str(link / "report.json"))[0], 2)
        self.assertFalse((outside / "report.json").exists())


if __name__ == "__main__":
    unittest.main()
