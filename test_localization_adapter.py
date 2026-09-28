"""Synthetic tests only. Run explicitly; never read/modify the game's INIs."""

from __future__ import annotations

import codecs
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import localization_adapter as adapter


def synthetic(raw: bytes, encoding: str | None = None) -> adapter.Source:
    return adapter.Source(adapter.WORK_ROOT / "translations.synthetic.ini",
                          adapter.sha256(raw), adapter.parse(raw, encoding))


class ParserTests(unittest.TestCase):
    def test_round_trip_formats_and_mixed_eols(self) -> None:
        text = ';comment\r\n[english]\n 01 =Hello=world;#\r2=""\n\n3=last'
        for bom, encoding in adapter.BOMS:
            with self.subTest(encoding=encoding):
                raw = bom + text.encode(encoding)
                source = synthetic(raw)
                bundle = adapter.export_bundle(source)
                self.assertEqual(adapter.build_bytes(source, bundle, ["english"]), raw)
                self.assertEqual(source.document.format.bom, bom)
                self.assertEqual(bundle["manifest"]["format"]["eol_counts"],
                                 {"CRLF": 1, "LF": 3, "CR": 1, "NONE": 1})
                self.assertFalse(bundle["manifest"]["format"]["final_eol"])
                for item in source.document.items:
                    self.assertEqual(raw[item.start:item.end].decode(encoding), item.value)

    def test_empty_and_bom_only(self) -> None:
        for raw in (b"", codecs.BOM_UTF8, codecs.BOM_UTF16_LE):
            doc = adapter.parse(raw)
            self.assertEqual(doc.raw, raw)
            self.assertEqual(doc.items, ())
            self.assertEqual(doc.lines, ())

    def test_bomless_and_multiple_edits(self) -> None:
        for encoding in adapter.ENCODINGS:
            with self.subTest(encoding=encoding):
                raw = "[english]\r1=A\r\n2=B\n".encode(encoding)
                source = synthetic(raw, encoding)
                bundle = adapter.export_bundle(source)
                self.assertEqual(adapter.build_bytes(source, bundle, ["english"]), raw)
                bundle["entries"][0]["target"] = "Longer"
                bundle["entries"][1]["target"] = ""
                self.assertEqual(adapter.build_bytes(source, bundle, ["english"]),
                                 "[english]\r1=Longer\r\n2=\n".encode(encoding))

    def test_encoding_refusals_and_explicit_legacy(self) -> None:
        with self.assertRaises(adapter.AdapterError):
            adapter.parse(b"[english]\n1=\xff")
        with self.assertRaises(adapter.AdapterError):
            adapter.parse(codecs.BOM_UTF8 + b"[english]", "cp1250")
        with self.assertRaises(adapter.AdapterError):
            adapter.parse("[english]\n1=Hi".encode("utf-16-le"))
        raw = "[english]\n1=\u0142".encode("cp1250")
        self.assertEqual(adapter.parse(raw, "cp1250").items[0].value, "\u0142")
        self.assertEqual(adapter.parse("[english]\n1=Hi".encode("utf-16-be"),
                                       "utf-16-be").items[0].value, "Hi")

    def test_reports_ambiguities_without_data_loss(self) -> None:
        raw = b"9=orphan\n[english]\n1=A\n01=B\n[english]\n2=C\ncontinued\n[English]\n2=D"
        source = synthetic(raw)
        bundle = adapter.export_bundle(source)
        report = bundle["manifest"]
        self.assertEqual(report["orphan_lines"], [1])
        self.assertEqual(report["duplicate_ids"][0]["lines"], [3, 4])
        self.assertEqual(report["duplicate_sections"][0]["lines"], [2, 5])
        self.assertEqual(report["unusual_lines"][0]["line"], 7)
        self.assertTrue(report["case_collisions"])
        self.assertEqual(adapter.build_bytes(source, bundle, ["english"]), raw)
        bundle["entries"][1]["target"] = "Changed"
        with self.assertRaises(adapter.AdapterError):
            adapter.build_bytes(source, bundle, ["english"])

    def test_empty_key_entry_is_preserved_and_globally_blocks_edits(self) -> None:
        raw = b"[english]\n  = \n1=Hello\n\t=\t\n"
        source = synthetic(raw)
        bundle = adapter.export_bundle(source)
        report = bundle["manifest"]
        self.assertEqual(report["line_kinds"]["empty-key-entry"], 2)
        self.assertEqual(report["empty_key_entries"], [
            {"line": 2, "text": "  = "}, {"line": 4, "text": "\t=\t"},
        ])
        self.assertEqual(adapter.build_bytes(source, bundle, ["english"]), raw)
        bundle["entries"][0]["target"] = "Changed"
        with self.assertRaises(adapter.AdapterError):
            adapter.build_bytes(source, bundle, ["english"])

    def test_duplicate_id_classification_blocks_identical_and_conflicting_values(self) -> None:
        for values, classification in ((b"A\n01=A", "identical"),
                                       (b"A\n01=B", "conflicting")):
            with self.subTest(classification=classification):
                source = synthetic(b"[english]\n1=" + values)
                bundle = adapter.export_bundle(source)
                duplicate = bundle["manifest"]["duplicate_ids"][0]
                self.assertEqual(duplicate["lines"], [2, 3])
                self.assertEqual(duplicate["classification"], classification)
                bundle["entries"][0]["target"] = "Changed"
                with self.assertRaises(adapter.AdapterError):
                    adapter.build_bytes(source, bundle, ["english"])

    def test_ids_in_different_languages_are_not_duplicates(self) -> None:
        report = adapter.manifest(synthetic(b"[english]\n1=A\n[french]\n1=B"))
        self.assertEqual(report["duplicate_ids"], [])
        self.assertEqual(report["ids_across_sections"], {"1": ["english", "french"]})
        self.assertEqual(report["id_inventory"], [
            {"section": "english", "section_line": 1, "id": "1", "line": 2},
            {"section": "french", "section_line": 3, "id": "1", "line": 4},
        ])
        self.assertEqual(report["edit_blockers"], [])


class ValidationTests(unittest.TestCase):
    def test_unicode_byte_splice_and_no_normalization(self) -> None:
        # Technical glyph fixture, not a translation of game content.
        glyphs = "\u0105\u0107\u0119\u0142\u0144\u00f3\u015b\u017a\u017c \u0104\u0106\u0118\u0141\u0143\u00d3\u015a\u0179\u017b a\u0328 \U0001f642"
        for bom, encoding in adapter.BOMS:
            with self.subTest(encoding=encoding):
                prefix = bom + "[english]\r\n 1 =".encode(encoding)
                suffix = "\r\n;keep\n[french]\r2=untouched".encode(encoding)
                source = synthetic(prefix + "Glyph fixture".encode(encoding) + suffix)
                bundle = adapter.export_bundle(source)
                bundle["entries"][0]["target"] = glyphs
                result = adapter.build_bytes(source, bundle, ["english"])
                self.assertEqual(result, prefix + glyphs.encode(encoding) + suffix)
                self.assertEqual(adapter.parse(result).items[0].value, glyphs)

    def test_target_only_complete_ordered_snapshot(self) -> None:
        source = synthetic(b"[english]\n1=Hello\n2=World")
        original = adapter.export_bundle(source)
        mutations = []
        for field, value in (("id", "8"), ("source", "fake"), ("line", True),
                             ("tokens", []), ("extra", "bad")):
            bundle = deepcopy(original)
            bundle["entries"][0][field] = value
            if bundle != original:
                mutations.append(bundle)
        bundle = deepcopy(original)
        bundle["entries"].reverse()
        mutations.append(bundle)
        bundle = deepcopy(original)
        bundle["entries"].pop()
        mutations.append(bundle)
        bundle = deepcopy(original)
        bundle["manifest"]["source"]["sha256"] = "0" * 64
        mutations.append(bundle)
        bundle = deepcopy(original)
        bundle["entries"][0]["target"] = 17
        mutations.append(bundle)
        for bundle in mutations:
            with self.subTest(bundle=bundle), self.assertRaises(adapter.AdapterError):
                adapter.build_bytes(source, bundle, ["english"])

    def test_scope(self) -> None:
        source = synthetic(b"[english]\n1=Hello\n[french]\n1=Bonjour")
        bundle = adapter.export_bundle(source)
        bundle["entries"][1]["target"] = "Changed"
        for sections in (["english"], [], ["missing"], ["french", "french"]):
            with self.subTest(sections=sections), self.assertRaises(adapter.AdapterError):
                adapter.build_bytes(source, bundle, sections)

    def test_token_order_parameters_and_unknown_syntax(self) -> None:
        source = synthetic(b"[english]\n1=[wave]Hi[/wave][delay] $ {0} %s \\n")
        bundle = adapter.export_bundle(source)
        bundle["entries"][0]["target"] = "[wave]Other[/wave][delay] $ {0} %s \\n"
        adapter.build_bytes(source, bundle, ["english"])
        targets = ("Hi", "[wave]Hi[/wave][delay] $ {1} %s \\n",
                   "[delay][wave]Hi[/wave] $ {0} %s \\n",
                   "[wave]Hi[/wave][delay] $$ {0} %s \\n")
        for target in targets:
            bundle["entries"][0]["target"] = target
            with self.subTest(target=target), self.assertRaises(adapter.AdapterError):
                adapter.build_bytes(source, bundle, ["english"])
        for text in ("[unverified]Text", "trailing\\", "open[", "<markup>Text"):
            source = synthetic(("[english]\n1=" + text).encode())
            bundle = adapter.export_bundle(source)
            self.assertEqual(adapter.build_bytes(source, bundle, ["english"]), source.document.raw)
            bundle["entries"][0]["target"] = text + " changed"
            with self.assertRaises(adapter.AdapterError):
                adapter.build_bytes(source, bundle, ["english"])

    def test_literal_and_percentage_percents_are_editable_but_printf_remains_protected(self) -> None:
        for value in ("%", "30%", "100 %", "% unfinished"):
            with self.subTest(value=value):
                source = synthetic(("[english]\n1=" + value).encode())
                bundle = adapter.export_bundle(source)
                self.assertEqual(bundle["entries"][0]["edit_blockers"], [])
                bundle["entries"][0]["target"] = "Edited " + value
                self.assertIn(b"Edited", adapter.build_bytes(source, bundle, ["english"]))
        source = synthetic(b"[english]\n1=Progress: %% %s")
        bundle = adapter.export_bundle(source)
        self.assertEqual([token["text"] for token in bundle["entries"][0]["tokens"]],
                         ["%%", "%s"])
        bundle["entries"][0]["target"] = "Progress: % %s"
        with self.assertRaises(adapter.AdapterError):
            adapter.build_bytes(source, bundle, ["english"])

    def test_syntax_controls_and_boundary_spaces(self) -> None:
        source = synthetic(b"[english]\n1=Hello")
        bundle = adapter.export_bundle(source)
        for target in ("Hi\n2=Injected", "Hi\r[other]", "Hi\x00", "Hi\ufffd",
                       "Hi\u202e", "Hi\u2028", "Hi\ud800", " Hi", "Hi ", '"Hi"', "Hi=there"):
            bundle["entries"][0]["target"] = target
            with self.subTest(target=repr(target)), self.assertRaises(adapter.AdapterError):
                adapter.build_bytes(source, bundle, ["english"])

    def test_empty_value_allowed_and_encoding_loss_refused(self) -> None:
        source = synthetic(b"[english]\n1=Hello", "cp1252")
        bundle = adapter.export_bundle(source)
        bundle["entries"][0]["target"] = ""
        self.assertEqual(adapter.build_bytes(source, bundle, ["english"]), b"[english]\n1=")
        bundle["entries"][0]["target"] = "\u0142"
        with self.assertRaises(adapter.AdapterError):
            adapter.build_bytes(source, bundle, ["english"])

    def test_observed_runtime_tags_are_preserved(self) -> None:
        value = ("[$7bffef][red][pulse][fa_center][change_skel,o_kendra_boss,idle]"
                 "[create_obj,o_movie_key_pop][follow_m][imposter_health]"
                 "[per_add,dago1][s_movie_key][s_slot,0][s_topui,1][say_hello]"
                 "[scale,.9][switch_song,mu_bar][zip,1522,1130][/red]")
        source = synthetic(("[english]\n1=" + value).encode())
        bundle = adapter.export_bundle(source)
        self.assertEqual(bundle["entries"][0]["edit_blockers"], [])
        bundle["entries"][0]["target"] = "Edited " + value
        self.assertIn(b"Edited", adapter.build_bytes(source, bundle, ["english"]))
    def test_inline_ini_ambiguity_is_read_only(self) -> None:
        for value in ('"Text"', "'Text'", "Text;comment", "Text#comment"):
            source = synthetic(("[english]\n1=" + value).encode())
            bundle = adapter.export_bundle(source)
            self.assertTrue(bundle["entries"][0]["edit_blockers"])
            self.assertEqual(adapter.build_bytes(source, bundle, ["english"]), source.document.raw)
            bundle["entries"][0]["target"] = value.replace("Text", "Changed")
            with self.assertRaises(adapter.AdapterError):
                adapter.build_bytes(source, bundle, ["english"])


class FilesystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="adapter-tests-")
        self.addCleanup(self.temp.cleanup)
        home = Path(self.temp.name) / "CrypyCustodian_PolishTranslation"
        home.mkdir()
        for name, value in (("ADAPTER_ROOT", home), ("WORK_ROOT", home / "work")):
            replacement = patch.object(adapter, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.root = adapter.work_directory()
        self.source_path = Path(self.temp.name) / "translations.synthetic.ini"
        self.raw = b"[english]\r\n1=Hello\n"
        self.source_path.write_bytes(self.raw)

    def new_name(self) -> str:
        name = "translations.test-" + uuid.uuid4().hex + ".ini"
        self.addCleanup((self.root / name).unlink, missing_ok=True)
        return name

    def test_source_identity_and_stale_guard(self) -> None:
        with self.assertRaises(adapter.AdapterError):
            adapter.load_source(self.source_path, "0" * 64)
        with self.assertRaises(adapter.AdapterError):
            adapter.load_source(Path("translations.ini"), adapter.sha256(self.raw))
        source = adapter.load_source(self.source_path, adapter.sha256(self.raw))
        name = self.new_name()
        output = adapter.publish(name, self.raw, "ini", source.verify)
        self.assertEqual(output.read_bytes(), self.raw)
        with self.assertRaises(adapter.AdapterError):
            adapter.publish(name, b"changed", "ini", source.verify)
        self.assertEqual(output.read_bytes(), self.raw)
        stale_name = self.new_name()
        self.source_path.write_bytes(self.raw + b"2=Changed")
        with self.assertRaises(adapter.AdapterError):
            adapter.publish(stale_name, self.raw, "ini", source.verify)
        self.assertFalse((self.root / stale_name).exists())

    def test_recheck_before_publication(self) -> None:
        source = adapter.load_source(self.source_path, adapter.sha256(self.raw))
        calls = 0

        def guard() -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                self.source_path.write_bytes(self.raw + b";changed")
            source.verify()

        name = self.new_name()
        before = set(self.root.glob(".adapter-*.tmp"))
        with self.assertRaises(adapter.AdapterError):
            adapter.publish(name, self.raw, "ini", guard)
        self.assertFalse((self.root / name).exists())
        self.assertEqual(set(self.root.glob(".adapter-*.tmp")), before)

    def test_refuse_traversal_ads_devices_and_data_win(self) -> None:
        for name in ("../translations.ini", "..\\translations.ini", "C:\\translations.ini",
                     "translations.ini:stream", "data.win", "NUL.json", "COM1.json", "a..json"):
            kind = "json" if name.endswith(".json") else "ini"
            with self.subTest(name=name), self.assertRaises(adapter.AdapterError):
                adapter.publish(name, b"x", kind, lambda: None)

    def test_publication_failure_has_no_final_file(self) -> None:
        name = self.new_name()
        before = set(self.root.glob(".adapter-*.tmp"))
        with patch.object(adapter.os, "link", side_effect=OSError("no hard links")):
            with self.assertRaises(OSError):
                adapter.publish(name, self.raw, "ini", lambda: None)
        self.assertFalse((self.root / name).exists())
        self.assertEqual(set(self.root.glob(".adapter-*.tmp")), before)

    def test_competing_output_is_not_overwritten(self) -> None:
        name = self.new_name()
        output = self.root / name
        real_link = adapter.os.link

        def competing_link(src: Path, dst: Path) -> None:
            with output.open("xb") as handle:
                handle.write(b"competitor")
            real_link(src, dst)

        with patch.object(adapter.os, "link", side_effect=competing_link):
            with self.assertRaises(FileExistsError):
                adapter.publish(name, self.raw, "ini", lambda: None)
        self.assertEqual(output.read_bytes(), b"competitor")

    def test_symlink_is_rejected_when_available(self) -> None:
        alias = Path(self.temp.name) / "translations.link.ini"
        try:
            alias.symlink_to(self.source_path)
        except (OSError, NotImplementedError):
            self.skipTest("System/uprawnienia nie pozwalają utworzyć symlinka.")
        with self.assertRaises(adapter.AdapterError):
            adapter.load_source(alias, adapter.sha256(self.raw))

    def test_json_duplicate_keys_nonfinite_and_invalid_utf8(self) -> None:
        path = Path(self.temp.name) / "corpus.json"
        for raw in (b'{"schema":1,"schema":2}', b'{"target":NaN}', b"\xff", b"[]"):
            path.write_bytes(raw)
            with self.subTest(raw=raw), self.assertRaises(adapter.AdapterError):
                adapter.load_corpus(path)

    def test_cli_inspect_writes_only_new_utf8_json_in_audit_directory(self) -> None:
        name = "inspect.test-" + uuid.uuid4().hex + ".json"
        audit = adapter.audit_directory()
        output = audit / name
        self.addCleanup(output.unlink, missing_ok=True)
        common = ["--source", str(self.source_path), "--expected-sha256", adapter.sha256(self.raw)]
        with patch("builtins.print") as printed:
            self.assertEqual(adapter.main(["inspect", *common, "--output", name]), 0)
            printed.assert_not_called()
        self.assertEqual(json.loads(output.read_bytes())["source"]["sha256"], adapter.sha256(self.raw))
        self.assertEqual(self.source_path.read_bytes(), self.raw)
        self.assertEqual(adapter.main(["inspect", *common, "--output", name]), 2)
        self.assertFalse((adapter.WORK_ROOT / name).exists())
        self.assertEqual(adapter.main(["inspect", *common, "--output", "../escape.json"]), 2)

    def test_cli_export_and_round_trip(self) -> None:
        export_name = "corpus.test-" + uuid.uuid4().hex + ".json"
        export_path = self.root / export_name
        self.addCleanup(export_path.unlink, missing_ok=True)
        output_name = self.new_name()
        common = ["--source", str(self.source_path), "--expected-sha256", adapter.sha256(self.raw)]
        with patch("builtins.print"):
            self.assertEqual(adapter.main(["export", *common, "--output", export_name]), 0)
            corpus = json.loads(export_path.read_bytes())
            self.assertIsNone(corpus["entries"][0]["target"])
            self.assertEqual(adapter.main(["build", *common, "--corpus", str(export_path),
                                           "--section", "english", "--output", output_name]), 0)
        self.assertEqual((self.root / output_name).read_bytes(), self.raw)
        self.assertEqual(self.source_path.read_bytes(), self.raw)


if __name__ == "__main__":
    unittest.main()
