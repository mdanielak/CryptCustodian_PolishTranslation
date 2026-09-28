"""Synthetic fixtures only; no game file access. Not executed on import."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import corpus_batches as batches
import localization_adapter as adapter


def fixture(raw: bytes, encoding: str | None = None):
    source = adapter.Source(adapter.WORK_ROOT / "translations.synthetic.ini",
                            adapter.sha256(raw), adapter.parse(raw, encoding))
    bundle = adapter.export_bundle(source)
    snapshot = batches.Snapshot(adapter.WORK_ROOT / "corpus" / "neutral.json",
                                adapter.json_text(bundle).encode("utf-8"), bundle)
    return source, snapshot


def documents(source, snapshot):
    return {name: json.loads(raw) for name, raw in batches.make_plan(source, snapshot).items()}


class SelectionTests(unittest.TestCase):
    def test_exact_selection_order_and_cross_language_ids(self):
        source, snapshot = fixture(b"9=orphan\n[french]\n1=F\n[english]\n10=A\n01=B\n0=C\n[german]\n1=G")
        original = deepcopy(snapshot.bundle)
        result = documents(source, snapshot)
        batch = result["batch-001.json"]
        self.assertEqual(batch["entry_indices"], [2, 3, 4])
        self.assertEqual([r["id"] for r in batch["entries"]], ["10", "01", "0"])
        self.assertEqual(batch["entries"], snapshot.bundle["entries"][2:5])
        self.assertTrue(all(r["target"] is None for r in batch["entries"]))
        self.assertEqual(snapshot.bundle, original)
        self.assertFalse(batch["canonical"])
        self.assertFalse(batch["build_input"])
        self.assertEqual(result["exclusions.json"]["global_anomalies"]["orphan_lines"], [1])

    def test_no_casefold_fallback_or_missing_section(self):
        for raw in (b"", b"[English]\n1=A", b"[french]\n1=A"):
            source, snapshot = fixture(raw)
            with self.subTest(raw=raw), self.assertRaises(adapter.AdapterError):
                batches.make_plan(source, snapshot)

    def test_all_occurrences_of_identical_conflicting_and_zero_alias_ids_excluded(self):
        for ids, values, classification in (
            (("1", "1"), ("A", "A"), "identical"),
            (("1", "1"), ("A", "B"), "conflicting"),
            (("01", "1", "0001"), ("A", "A", "A"), "identical"),
            (("01", "1"), ("A", "B"), "conflicting"),
            (("00", "0", "000"), ("A", "B", "C"), "conflicting"),
            (("1", "1"), ("ą", "a\u0328"), "conflicting"),
        ):
            raw = ("[english]\n" + "\n".join(f"{key}={value}" for key, value in zip(ids, values))
                   + "\n99=Safe\n[french]\n99=Other").encode()
            with self.subTest(ids=ids, values=values):
                source, snapshot = fixture(raw)
                result = documents(source, snapshot)
                self.assertEqual(result["batch-001.json"]["entry_indices"], [len(ids)])
                excluded = result["exclusions.json"]["quarantine"]
                self.assertEqual([r["entry_index"] for r in excluded], list(range(len(ids))))
                for record in excluded:
                    detail = record["reasons"][0]["details"]
                    self.assertEqual(detail["classification"], classification)
                    self.assertEqual(detail["numeric_id"], ids[0].lstrip("0") or "0")
                    self.assertEqual(record["entry"], snapshot.bundle["entries"][record["entry_index"]])

    def test_casefold_collision_quarantines_whole_exact_section(self):
        for alias in ("English", "ENGLISH", "engli\u017fh"):
            for first in (True, False):
                exact = "[english]\n1=A\n2=B\n"
                other = f"[{alias}]\n99=C\n"
                source, snapshot = fixture(((other + exact) if first else (exact + other)).encode())
                with self.subTest(alias=alias, first=first):
                    result = documents(source, snapshot)
                    counts = result["index.json"]["counts"]
                    self.assertEqual((counts["selected"], counts["accepted"], counts["quarantined"]), (2, 0, 2))
                    self.assertEqual(counts["outside_section"], 1)
                    self.assertEqual(counts["batches"], 0)
                    for record in result["exclusions.json"]["quarantine"]:
                        self.assertEqual(record["entry"]["section"], "english")
                        self.assertEqual(record["reasons"][0]["code"], "case-ambiguous-section")

    def test_other_section_casefold_collision_is_global_only(self):
        source, snapshot = fixture("[english]\n1=A\n[Stra\u00dfe]\n1=B\n[STRASSE]\n1=C".encode())
        result = documents(source, snapshot)
        self.assertEqual(result["index.json"]["counts"]["accepted"], 1)
        self.assertEqual(result["exclusions.json"]["global_anomalies"]["case_collisions"],
                         [["Stra\u00dfe", "STRASSE"]])

    def test_repeated_english_headers_not_silently_merged(self):
        source, snapshot = fixture(b"[english]\n1=A\n[english]\n2=B")
        result = documents(source, snapshot)
        self.assertEqual(result["index.json"]["counts"]["quarantined"], 2)
        self.assertTrue(all(r["reasons"][0]["code"] == "repeated-section"
                            for r in result["exclusions.json"]["quarantine"]))

    def test_local_blockers_and_combined_reasons(self):
        blocked = ['"Q"', "A;B", "A#B", "[unknown]A", "'A", "<A>", "A\u202e", "A\ufffd"]
        text = "[english]\n1=30%\n" + "\n".join(f"{i + 2}={v}" for i, v in enumerate(blocked))
        text += "\n2=Duplicate\n99=Safe"
        source, snapshot = fixture(text.encode())
        result = documents(source, snapshot)
        self.assertEqual([e["id"] for e in result["batch-001.json"]["entries"]], ["1", "99"])
        excluded = result["exclusions.json"]["quarantine"]
        self.assertEqual(len(excluded), len(blocked) + 1)
        self.assertEqual([r["code"] for r in excluded[0]["reasons"]],
                         ["local-edit-blockers", "duplicate-numeric-id"])
        for record in excluded[:-1]:
            self.assertEqual(record["reasons"][0]["details"], record["entry"]["edit_blockers"])

    def test_partition_sizes_order_and_every_hash(self):
        for size in (0, 1, 99, 100, 101, 200, 201, 1328):
            text = "[french]\n1=Outside\n[english]\n" + "\n".join(
                f'{i}=' + ('"Quarantine"' if i == 1270 else f"Fixture {i}")
                for i in range(1, size + 1))
            source, snapshot = fixture(text.encode())
            with self.subTest(size=size):
                plan = batches.make_plan(source, snapshot)
                result = {n: json.loads(v) for n, v in plan.items()}
                index = result["index.json"]
                report = result["completeness.json"]
                accepted = report["accepted_entry_indices"]
                quarantine = report["quarantined_entry_indices"]
                selected = report["selected_entry_indices"]
                self.assertEqual(selected, list(range(1, size + 1)))
                self.assertEqual(sorted(accepted + quarantine), selected)
                self.assertFalse(set(accepted) & set(quarantine))
                self.assertTrue(all(report["checks"].values()))
                flattened = [i for b in index["batches"] for i in b["entry_indices"]]
                self.assertEqual(flattened, accepted)
                self.assertEqual(len(flattened), len(set(flattened)))
                for ref in [*index["batches"], index["exclusions"], index["completeness"]]:
                    payload = plan[ref["file"]]
                    self.assertEqual(ref["sha256"], adapter.sha256(payload))
                    self.assertEqual(ref["size_bytes"], len(payload))
                for ref in index["batches"]:
                    batch = result[ref["file"]]
                    self.assertEqual(batch["entry_indices"], ref["entry_indices"])
                    self.assertEqual(batch["entries"], [snapshot.bundle["entries"][i] for i in ref["entry_indices"]])
                if size == 1328:  # Shape of acceptance criterion, NOT real game content.
                    self.assertEqual(index["counts"]["accepted"], 1327)
                    self.assertEqual(quarantine, [1270])
                    self.assertEqual([b["count"] for b in index["batches"]], [100] * 13 + [27])

    def test_unicode_nfc_nfd_polish_bom_spans_tokens_and_provenance(self):
        glyphs = "ąćęłńóśźż ĄĆĘŁŃÓŚŹŻ"
        values = [glyphs, "a\u0328c\u0301e\u0328", "[wave] ą {0} %s \\n\t", "🙂"]
        text = ";comment\r\n[english]\n" + "\r".join(f" {i:02} =" + v for i, v in enumerate(values))
        for bom, encoding in adapter.BOMS:
            raw = bom + text.encode(encoding)
            source, snapshot = fixture(raw)
            with self.subTest(encoding=encoding):
                result = documents(source, snapshot)
                entries = result["batch-001.json"]["entries"]
                self.assertEqual(entries, snapshot.bundle["entries"])
                self.assertEqual([e["source"] for e in entries], values)
                for entry in entries:
                    start, end = entry["value_byte_span"]
                    self.assertEqual(entry["value_sha256"], adapter.sha256(raw[start:end]))
                    self.assertEqual(raw[start:end].decode(encoding), entry["source"])
                self.assertEqual(result["index.json"]["source"], snapshot.bundle["manifest"]["source"])
                self.assertEqual(result["index.json"]["snapshot"]["sha256"], adapter.sha256(snapshot.raw))
                self.assertEqual(adapter.build_bytes(source, snapshot.bundle, ["english"]), raw)

    def test_every_batch_schema_is_rejected_by_build_even_when_complete(self):
        source, snapshot = fixture(b"[english]\n1=A")
        result = documents(source, snapshot)
        for artifact in result.values():
            with self.assertRaises(adapter.AdapterError):
                adapter.build_bytes(source, artifact, ["english"])
        disguised = deepcopy(snapshot.bundle)
        disguised["schema"] = batches.BATCH_SCHEMA
        with self.assertRaises(adapter.AdapterError):
            adapter.build_bytes(source, disguised, ["english"])

    def test_global_blockers_allow_preparation_not_build_edits(self):
        for suffix in ("\n = \n", "\ncontinued", "\n[french]\n1=X\n01=X",
                       "\n[french]\n1=X\n01=Y", "\n[french]\n[French]"):
            source, snapshot = fixture(("[english]\n1=Safe" + suffix).encode())
            with self.subTest(suffix=suffix):
                result = documents(source, snapshot)
                self.assertEqual(result["index.json"]["counts"]["accepted"], 1)
                self.assertEqual(result["exclusions.json"]["global_anomalies"]["edit_blockers"],
                                 snapshot.bundle["manifest"]["edit_blockers"])
                self.assertEqual(adapter.build_bytes(source, snapshot.bundle, ["english"]), source.document.raw)
                edited = deepcopy(snapshot.bundle)
                edited["entries"][0]["target"] = "Changed"
                with self.assertRaisesRegex(adapter.AdapterError, "Niejednoznaczna struktura"):
                    adapter.build_bytes(source, edited, ["english"])


class FilesystemTests(unittest.TestCase):
    def setUp(self):
        # All fixture writes are sandboxed beneath actual work/, never a game INI.
        temp = tempfile.TemporaryDirectory(prefix="batch-tests-")
        self.addCleanup(temp.cleanup)
        home = Path(temp.name) / "CrypyCustodian_PolishTranslation"
        home.mkdir()
        for attribute, value in (("ADAPTER_ROOT", home), ("WORK_ROOT", home / "work")):
            patcher = patch.object(adapter, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.path = adapter.work_directory() / "translations.synthetic.ini"
        self.raw = b"[english]\r\n1=Hello\n2=World\n"
        self.path.write_bytes(self.raw)
        self.source = adapter.load_source(self.path, adapter.sha256(self.raw))
        self.export = batches.export_snapshot(self.source, "neutral.json")
        self.digest = adapter.sha256(self.export.read_bytes())
        self.snapshot = batches.load_snapshot(self.export, self.digest, self.source)

    def test_export_is_unchanged_adapter_workflow_and_successful_verification(self):
        self.assertEqual(self.export.parent, adapter.WORK_ROOT / "corpus")
        self.assertEqual(self.export.read_bytes(), adapter.json_text(adapter.export_bundle(self.source)).encode())
        self.assertFalse(self.snapshot.bundle["manifest"]["source"]["canonical"])
        index = batches.prepare(self.source, self.snapshot, "english-001")
        self.assertEqual(index.parent.parent, self.export.parent)
        before = {p: p.read_bytes() for p in index.parent.iterdir()}
        self.assertEqual(batches.prepare(self.source, self.snapshot, "english-001", verify_only=True), index)
        self.assertEqual(before, {p: p.read_bytes() for p in index.parent.iterdir()})
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_no_clobber_snapshot_run_and_existing_file(self):
        original = self.export.read_bytes()
        with self.assertRaises(adapter.AdapterError):
            batches.export_snapshot(self.source, "neutral.json")
        self.assertEqual(self.export.read_bytes(), original)
        index = batches.prepare(self.source, self.snapshot, "run")
        before = {p: p.read_bytes() for p in index.parent.iterdir()}
        with self.assertRaises(FileExistsError):
            batches.prepare(self.source, self.snapshot, "run")
        self.assertEqual(before, {p: p.read_bytes() for p in index.parent.iterdir()})
        with self.assertRaises(FileExistsError):
            batches.prepare(self.source, self.snapshot, "neutral.json")
        self.assertEqual(self.export.read_bytes(), original)
        (self.export.parent / "empty").mkdir()
        with self.assertRaises(FileExistsError):
            batches.prepare(self.source, self.snapshot, "empty")
        self.assertEqual(list((self.export.parent / "empty").iterdir()), [])

    def test_unsafe_paths_and_hashes(self):
        for name in ("../escape", "..\\escape", "C:\\escape", "x:ads", "NUL", "COM1", "a..b", "", "end."):
            with self.subTest(name=name), self.assertRaises(adapter.AdapterError):
                batches.prepare(self.source, self.snapshot, name)
        for path in (Path("neutral.json"), self.export.parent / ".." / "corpus" / "neutral.json"):
            with self.assertRaises(adapter.AdapterError):
                batches.load_snapshot(path, self.digest, self.source)
        outside = adapter.WORK_ROOT / "outside.json"
        outside.write_bytes(self.snapshot.raw)
        with self.assertRaises(adapter.AdapterError):
            batches.load_snapshot(outside, self.digest, self.source)
        for digest in ("0" * 64, "bad"):
            with self.assertRaises(adapter.AdapterError):
                batches.load_snapshot(self.export, digest, self.source)
            with self.assertRaises(adapter.AdapterError):
                adapter.load_source(self.path, digest)

    def test_snapshot_tampering_rejected_even_with_recomputed_hash(self):
        mutations = []
        for field, value in (("id", "01"), ("line", True), ("section_line", True),
                             ("section", "English"), ("source", "Forged"),
                             ("value_byte_span", [0, 1]), ("value_sha256", "0" * 64),
                             ("tokens", [1]), ("edit_blockers", ["fake"]),
                             ("target", "Hello"), ("extra", None)):
            changed = deepcopy(self.snapshot.bundle)
            changed["entries"][0][field] = value
            mutations.append(changed)
        for field, value in (("sha256", "0" * 64), ("path", "fake"), ("canonical", True)):
            changed = deepcopy(self.snapshot.bundle)
            changed["manifest"]["source"][field] = value
            mutations.append(changed)
        changed = deepcopy(self.snapshot.bundle)
        changed["entries"].reverse()
        mutations.append(changed)
        changed = deepcopy(self.snapshot.bundle)
        changed["entries"].pop()
        mutations.append(changed)
        for changed in mutations:
            payload = adapter.json_text(changed).encode()
            self.export.write_bytes(payload)
            with self.assertRaises(adapter.AdapterError):
                batches.load_snapshot(self.export, adapter.sha256(payload), self.source)

    def test_strict_json_and_snapshot_byte_hash(self):
        for payload in (b'{}', b'{"schema":1,"schema":2}', b'{"x":NaN}', b'[]', b'\xff'):
            self.export.write_bytes(payload)
            with self.assertRaises(adapter.AdapterError):
                batches.load_snapshot(self.export, adapter.sha256(payload), self.source)
        # Whitespace is semantically allowed but requires its own exact byte hash.
        payload = self.snapshot.raw + b"\n"
        self.export.write_bytes(payload)
        with self.assertRaises(adapter.AdapterError):
            batches.load_snapshot(self.export, self.digest, self.source)
        batches.load_snapshot(self.export, adapter.sha256(payload), self.source)

    def test_stale_source_or_snapshot_before_and_during_publication(self):
        for target in (self.path, self.export):
            original = target.read_bytes()
            target.write_bytes(original + b" ")
            with self.assertRaises(adapter.AdapterError):
                batches.prepare(self.source, self.snapshot, "stale")
            self.assertFalse((self.export.parent / "stale").exists())
            target.write_bytes(original)
        real_publish = adapter.publish
        for number, target in enumerate((self.path, self.export)):
            original = target.read_bytes()

            def publish_with_change(name, payload, kind, guard, root):
                calls = 0

                def changing_guard():
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        target.write_bytes(original + b" ")
                    guard()

                return real_publish(name, payload, kind, changing_guard, root)

            with patch.object(adapter, "publish", side_effect=publish_with_change):
                with self.assertRaises(adapter.AdapterError):
                    batches.prepare(self.source, self.snapshot, f"during-{number}")
            directory = self.export.parent / f"during-{number}"
            self.assertEqual(list(directory.iterdir()), [])
            target.write_bytes(original)

    def test_failed_run_has_no_completion_marker_and_no_automatic_resume(self):
        real_publish = adapter.publish

        def fail_second(name, *args, **kwargs):
            if name == "exclusions.json":
                raise OSError("synthetic failure")
            return real_publish(name, *args, **kwargs)

        with patch.object(adapter, "publish", side_effect=fail_second), self.assertRaises(OSError):
            batches.prepare(self.source, self.snapshot, "failed")
        directory = self.export.parent / "failed"
        self.assertTrue((directory / "batch-001.json").is_file())
        self.assertFalse((directory / "index.json").exists())
        with self.assertRaises(FileExistsError):
            batches.prepare(self.source, self.snapshot, "failed")
        with self.assertRaises(adapter.AdapterError):
            batches.prepare(self.source, self.snapshot, "failed", verify_only=True)

    def test_readback_detects_tampering_before_index_publication(self):
        real_publish = adapter.publish

        def publish_then_tamper(name, payload, kind, guard, root):
            output = real_publish(name, payload, kind, guard, root)
            if name == "completeness.json":
                (root / "batch-001.json").write_bytes(b"{}")
            return output

        with patch.object(adapter, "publish", side_effect=publish_then_tamper):
            with self.assertRaises(adapter.AdapterError):
                batches.prepare(self.source, self.snapshot, "readback")
        self.assertFalse((self.export.parent / "readback" / "index.json").exists())

    def test_artifact_tampering_missing_extra_and_hash_forgery(self):
        index = batches.prepare(self.source, self.snapshot, "tamper")
        for path in index.parent.iterdir():
            original = path.read_bytes()
            path.write_bytes(original + b" ")
            with self.assertRaises(adapter.AdapterError):
                batches.prepare(self.source, self.snapshot, "tamper", verify_only=True)
            path.write_bytes(original)
        batch = index.parent / "batch-001.json"
        original_batch = batch.read_bytes()
        original_index = index.read_bytes()
        changed = json.loads(original_batch)
        changed["entries"][0]["target"] = "Forged"
        payload = adapter.json_text(changed).encode()
        batch.write_bytes(payload)
        forged = json.loads(original_index)
        forged["batches"][0].update(sha256=adapter.sha256(payload), size_bytes=len(payload))
        index.write_bytes(adapter.json_text(forged).encode())
        with self.assertRaises(adapter.AdapterError):
            batches.prepare(self.source, self.snapshot, "tamper", verify_only=True)
        index.write_bytes(original_index)
        batch.unlink()
        with self.assertRaises(adapter.AdapterError):
            batches.prepare(self.source, self.snapshot, "tamper", verify_only=True)
        batch.write_bytes(original_batch)
        (index.parent / "extra.json").write_bytes(b"{}")
        with self.assertRaises(adapter.AdapterError):
            batches.prepare(self.source, self.snapshot, "tamper", verify_only=True)

    def test_competing_artifact_and_hardlink_failure(self):
        real_link = adapter.os.link

        def competing_link(src, dst):
            with Path(dst).open("xb") as handle:
                handle.write(b"competitor")
            real_link(src, dst)

        with patch.object(adapter.os, "link", side_effect=competing_link), self.assertRaises(FileExistsError):
            batches.prepare(self.source, self.snapshot, "race")
        directory = self.export.parent / "race"
        self.assertEqual((directory / "batch-001.json").read_bytes(), b"competitor")
        self.assertFalse((directory / "index.json").exists())
        with patch.object(adapter.os, "link", side_effect=OSError("no hardlinks")), self.assertRaises(OSError):
            batches.prepare(self.source, self.snapshot, "nolink")
        self.assertEqual(list((self.export.parent / "nolink").iterdir()), [])

    def test_symlinks_refused_when_supported(self):
        alias = self.export.parent / "alias.json"
        try:
            alias.symlink_to(self.export)
        except (OSError, NotImplementedError):
            self.skipTest("Brak uprawnień/obsługi symlinków.")
        with self.assertRaises(adapter.AdapterError):
            batches.load_snapshot(alias, self.digest, self.source)
        directory = self.export.parent / "linked"
        directory.symlink_to(adapter.WORK_ROOT, target_is_directory=True)
        with self.assertRaises(adapter.AdapterError):
            batches.prepare(self.source, self.snapshot, "linked")

    def test_cli_prepare_verify_and_refusal(self):
        common = ["--source", str(self.path), "--expected-sha256", self.source.expected_hash,
                  "--snapshot", str(self.export), "--expected-snapshot-sha256", self.digest,
                  "--run", "cli"]
        with patch("builtins.print"):
            self.assertEqual(batches.main(["prepare", *common]), 0)
            self.assertEqual(batches.main(["verify", *common]), 0)
            self.assertEqual(batches.main(["prepare", *common]), 2)
            self.assertEqual(batches.main([
                "export", "--source", str(self.path), "--expected-sha256", self.source.expected_hash,
                "--output", "cli-neutral.json",
            ]), 0)
        self.assertEqual((self.export.parent / "cli-neutral.json").read_bytes(), self.snapshot.raw)


if __name__ == "__main__":
    unittest.main()
