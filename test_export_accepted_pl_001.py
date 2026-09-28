"""Pinned real-input integration + in-memory mutations; no game/release writes."""

from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import export_accepted_pl_001 as release
import localization_adapter as adapter


@unittest.skipUnless(os.environ.get("CC_EXPORT_INTEGRATION") == "1",
                     "Pinned private inputs required; opt in with CC_EXPORT_INTEGRATION=1")
class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = release.arguments(["--export"])
        cls.context = release.load_context(cls.paths)
        cls.records = cls.context.accepted.value["entries"]
        cls.output, cls.report = release.build_candidate(cls.context, cls.records)

    def records_copy(self):
        return deepcopy(self.records)

    def reject(self, records, message):
        with self.assertRaisesRegex(adapter.AdapterError, message):
            release.build_candidate(self.context, records)

    def test_complete_and_readback(self):
        self.assertEqual(1327, self.report["records"])
        self.assertEqual(set(self.context.indices), {r["entry_index"] for r in self.records})
        after = adapter.parse(self.output)
        for r in self.records:
            self.assertEqual(r["target"], after.items[r["entry_index"]].value)
        self.context.verify()

    def test_noop_byte_identity(self):
        records = [{**r, "target": self.context.source.document.items[r["entry_index"]].value}
                   for r in self.records]
        result, report = release.build_candidate(self.context, records)
        self.assertEqual(self.context.source.document.raw, result)
        self.assertEqual([], report["changes"])

    def test_ranges_and_inverse_independent(self):
        original = self.context.source.document.raw
        inverse = self.output
        for change in reversed(self.report["changes"]):
            a, b = change["source_value_span"]
            c, d = change["output_value_span"]
            item = self.context.source.document.items[change["entry_index"]]
            self.assertEqual((item.start, item.end), (a, b))
            self.assertEqual("english", item.section)
            inverse = inverse[:c] + original[a:b] + inverse[d:]
        self.assertEqual(original, inverse)
        release.verify_result(original, self.output, self.report["changes"])

    def test_empty_keys_duplicates_quarantine_foreign_unchanged(self):
        before = self.context.source.document
        after = adapter.parse(self.output)
        empty = [line.number for line in before.lines if line.kind == "empty-key-entry"]
        manifest = self.context.snapshot.bundle["manifest"]
        duplicates = [n for d in manifest["duplicate_ids"] for n in d["lines"]]
        quarantine = [i.line for i in before.items if i.key == "1270"]
        foreign = [i.line for i in before.items if i.section != "english"]
        self.assertEqual(31, len(empty))
        self.assertEqual(42, len(duplicates))
        for number in set(empty + duplicates + quarantine + foreign):
            a, b = before.lines[number - 1], after.lines[number - 1]
            self.assertEqual(before.raw[a.start:a.end], self.output[b.start:b.end])

    def test_unicode_coverage_utf8_crlf(self):
        self.assertTrue(all(self.report["unicode_coverage"].values()))
        self.assertEqual(18, len(self.report["unicode_coverage"]))
        self.assertEqual(self.output, self.output.decode("utf-8").encode("utf-8"))
        self.assertNotIn(b"\n", self.output.replace(b"\r\n", b""))
        self.assertNotIn(b"\r", self.output.replace(b"\r\n", b""))

    def test_no_unicode_normalization(self):
        value = "Zaz\u0307o\u0301łc\u0301 gęślą jaźń"
        self.assertEqual(value.encode("utf-8"), release.validate_target("Text", value, "1"))

    def test_input_hash_refusals(self):
        for name in ("SOURCE_SHA", "SNAPSHOT_SHA", "ACCEPTED_SHA"):
            with self.subTest(name=name), patch.object(release, name, "0" * 64):
                with self.assertRaisesRegex(adapter.AdapterError, "SHA-256"):
                    release.load_context(self.paths)

    def test_provenance_hash_refusal(self):
        original = release.workflow.load_json
        def changed(path, digest, **kwargs):
            result = original(path, digest, **kwargs)
            if path.name == "accepted-pl-001.json":
                value = deepcopy(result.value)
                value["provenance"][0]["sha256"] = "0" * 64
                return replace(result, value=value)
            return result
        with patch.object(release.workflow, "load_json", side_effect=changed):
            with self.assertRaisesRegex(adapter.AdapterError, "SHA-256"):
                release.load_context(self.paths)

    def test_missing_and_extra_records(self):
        self.reject(self.records[:-1], "1327")
        self.reject(self.records + [self.records[0]], "1327")

    def test_duplicate_index(self):
        records = self.records_copy()
        records[1] = deepcopy(records[0])
        self.reject(records, "entry_index")

    def test_index_not_id(self):
        records = self.records_copy()
        records[0]["entry_index"] = records[1]["entry_index"]
        self.reject(records, "id/source/value")

    def test_bool_index(self):
        records = self.records_copy()
        records[0]["entry_index"] = False
        self.reject(records, "entry_index")

    def test_id_and_value_hash(self):
        for field, value in (("id", "99999"), ("value_sha256", "0" * 64)):
            records = self.records_copy()
            records[0][field] = value
            self.reject(records, "id/source/value")

    def test_quarantine_and_foreign_section_indices(self):
        for item_index in (next(i for i, v in enumerate(self.context.source.document.items)
                                if v.section == "english" and v.key == "1270"),
                           next(i for i, v in enumerate(self.context.source.document.items)
                                if v.section != "english")):
            records = self.records_copy()
            records[0]["entry_index"] = item_index
            self.reject(records, "entry_index")

    def test_snapshot_span_source_section_binding(self):
        for field, value in (("value_byte_span", [0, 1]), ("source", "wrong"),
                             ("section", "polish"), ("section_line", 999), ("line", 999)):
            bundle = deepcopy(self.context.snapshot.bundle)
            bundle["entries"][0][field] = value
            context = replace(self.context, snapshot=replace(self.context.snapshot, bundle=bundle))
            with self.subTest(field=field), self.assertRaises(adapter.AdapterError):
                release.build_candidate(context, self.records)

    def test_newline_nul_and_invalid_unicode(self):
        for char in ("\n", "\r", "\r\n", "\x00", "\ud800", "\u2028", "\ufffd"):
            records = self.records_copy()
            records[0]["target"] += char
            self.reject(records, "Newline/NUL|Niedozwolony")

    def test_historical_tokens_and_order(self):
        source = "[wave]A % s % c $~@[/wave][delay]"
        target = "[wave]Ż % s % c $~@[/wave][delay]"
        self.assertEqual(target.encode("utf-8"), release.validate_target(source, target, "1"))
        for old, new in (("% s", "%s"), ("% c", "%c"), ("$", ""), ("~", ""),
                         ("@", ""), ("$~@", "@$~"), ("[wave]", "[red]"),
                         ("% s % c", "% c % s")):
            with self.subTest(old=old), self.assertRaisesRegex(adapter.AdapterError, "tokeny"):
                release.validate_target(source, target.replace(old, new), "1")
        with self.assertRaises(adapter.AdapterError):
            release.validate_target("[scale,1]x", "[scale,2]y", "1")

    def test_id_1901_at_explicit(self):
        records = self.records_copy()
        record = next(r for r in records if r["id"] == "1901")
        self.assertEqual(1, record["target"].count("@"))
        record["target"] = record["target"].replace("@", "")
        self.reject(records, "ID 1901")

    def test_boundary_spaces(self):
        records = self.records_copy()
        records[0]["target"] += " "
        self.reject(records, "brzegowe spacje")

    def test_tampered_nonvalue_and_value_output_refused(self):
        original = self.context.source.document.raw
        for offset in (0, self.report["changes"][0]["output_value_span"][0], len(self.output) - 1):
            mutated = self.output[:offset] + bytes([self.output[offset] ^ 1]) + self.output[offset + 1:]
            with self.assertRaises(adapter.AdapterError):
                release.verify_result(original, mutated, self.report["changes"])

    def test_default_build_still_refuses(self):
        bundle = deepcopy(self.context.snapshot.bundle)
        bundle["entries"][0]["target"] = self.records[0]["target"]
        with self.assertRaisesRegex(adapter.AdapterError, "Niejednoznaczna struktura"):
            adapter.build_bytes(self.context.source, bundle, ["english"])

class PortableExportTests(unittest.TestCase):
    def test_cli_env_and_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            paths = release.arguments(["--export"])
            self.assertEqual(Path("inputs/translations.ini").absolute(), paths.source)
            self.assertEqual(Path(tempfile.gettempdir()) / "crypt-custodian-export", paths.output_root)
        with patch.dict(os.environ, {"CC_EXPORT_SOURCE": "env.ini", "CC_EXPORT_OUTPUT_ROOT": "output"}):
            paths = release.arguments(["--export", "--source", "zażółć.ini",
                                       "--snapshot", "snapshot.json", "--accepted", "accepted.json"])
            self.assertEqual(Path("zażółć.ini").absolute(), paths.source)
            self.assertEqual(Path("output").absolute(), paths.output_root)
            self.assertEqual(Path("snapshot.json").absolute(), paths.snapshot)
            self.assertEqual(Path("accepted.json").absolute(), paths.accepted)

    def test_source_hash_is_not_overridable(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "translations.ini"
            source.write_bytes(b"[english]\r\n1=synthetic\r\n")
            paths = release.arguments(["--export", "--source", str(source)])
            with self.assertRaisesRegex(adapter.AdapterError, "SHA-256"):
                release.load_context(paths)

    def test_polish_roundtrip_without_normalization(self):
        for text in (release.POLISH, "Zaz\u0307o\u0301łc\u0301"):
            self.assertEqual(text.encode("utf-8"), release.validate_target("Text", text, "1"))

    def test_existing_output_and_guard_refused(self):
        with tempfile.TemporaryDirectory(prefix="cc-export-test-") as tmp:
            root = Path(tmp)
            path = adapter.publish("translations.ini", b"sentinel", "ini", lambda: None, root)
            with self.assertRaisesRegex(adapter.AdapterError, "istnieje"):
                adapter.publish("translations.ini", b"new", "ini", lambda: None, root)
            self.assertEqual(b"sentinel", path.read_bytes())
            def refusal():
                raise adapter.AdapterError("guard refused")
            with self.assertRaisesRegex(adapter.AdapterError, "guard refused"):
                adapter.publish("translations-new.ini", b"new", "ini", refusal, root)
            self.assertFalse((root / "translations-new.ini").exists())

    def test_no_clobber_race(self):
        with tempfile.TemporaryDirectory(prefix="cc-export-test-") as tmp:
            root = Path(tmp)
            calls = 0
            def race():
                nonlocal calls
                calls += 1
                if calls == 2:
                    with (root / "translations.ini").open("xb") as handle:
                        handle.write(b"racer")
            with self.assertRaises(FileExistsError):
                adapter.publish("translations.ini", b"replacement", "ini", race, root)
            self.assertEqual(b"racer", (root / "translations.ini").read_bytes())

    def test_next_free_directory(self):
        with tempfile.TemporaryDirectory(prefix="cc-export-test-") as tmp:
            root = Path(tmp)
            first = release.reserve_release(root / "runs", root / "inputs/source.ini")
            (first / "sentinel").write_bytes(b"keep")
            second = release.reserve_release(root / "runs", root / "inputs/source.ini")
            self.assertEqual("release", first.name)
            self.assertEqual("release-002", second.name)
            self.assertEqual(b"keep", (first / "sentinel").read_bytes())

    def test_protected_and_invalid_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for output in (root / "runs", root / "missing/runs", Path("relative"),
                           adapter.ADAPTER_ROOT / "export-output"):
                with self.subTest(output=output), self.assertRaises(adapter.AdapterError):
                    release.reserve_release(output, root / "source.ini")
            self.assertFalse((root / "runs").exists())


if __name__ == "__main__":
    unittest.main()
