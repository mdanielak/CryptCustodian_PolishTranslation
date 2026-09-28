"""Synthetic tests only; all fixture I/O beneath work/translation/tests-*/.

Not executed on import. Never read the game's INI or existing real batches.
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import corpus_batches as batches
import localization_adapter as adapter
import translation_workflow as workflow


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="tests-")
        self.addCleanup(temp.cleanup)
        home = Path(temp.name) / "CrypyCustodian_PolishTranslation"
        home.mkdir()
        (home / "work").mkdir()
        for attribute, value in (("ADAPTER_ROOT", home), ("WORK_ROOT", home / "work")):
            patcher = patch.object(adapter, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.source_path = adapter.WORK_ROOT / "translations.synthetic.ini"
        self.source_raw = (
            b"[french]\r\n1=Outside\n[english]\r\n"
            b"01=Hello [wave]{0} %s \\n[/wave]\n2=World\n3= Spaced \n"
            b"4=Tab\tstop\n5=[unknown]Blocked\n = \n"
        )
        self.source_path.write_bytes(self.source_raw)
        self.source = adapter.load_source(self.source_path, adapter.sha256(self.source_raw))
        self.snapshot_path = batches.export_snapshot(self.source, "neutral.json")
        self.snapshot_raw = self.snapshot_path.read_bytes()
        self.snapshot = batches.load_snapshot(self.snapshot_path, adapter.sha256(self.snapshot_raw), self.source)
        self.plan = batches.make_plan(self.source, self.snapshot)
        self.run_directory = self.snapshot_path.parent / "synthetic-001"
        self.run_directory.mkdir()
        # Only the chosen existing batch is needed, not a new prepare/verify run.
        self.batch_path = self.run_directory / "batch-001.json"
        self.batch_raw = self.plan[self.batch_path.name]
        self.batch_path.write_bytes(self.batch_raw)
        self.context = self.load_context()
        self.assignment = workflow.assignment_document(self.context)
        self.assignment_raw = adapter.json_text(self.assignment).encode()
        self.assignment_hash = adapter.sha256(self.assignment_raw)
        self.response = {
            "schema": workflow.RESPONSE_SCHEMA, "assignment_sha256": self.assignment_hash,
            "entries": [{"entry_index": r["entry_index"], "id": r["id"],
                         "value_sha256": r["value_sha256"], "target": r["source"],
                         "uncertain": False, "notes": ""} for r in self.assignment["entries"]],
        }

    def load_context(self, *, batch_hash=None, snapshot_hash=None, source_hash=None):
        return workflow.load_context(
            self.source_path, source_hash or adapter.sha256(self.source_raw),
            self.snapshot_path, snapshot_hash or adapter.sha256(self.snapshot_raw),
            self.batch_path, batch_hash or adapter.sha256(self.batch_raw),
        )

    def check_response(self, response=None, assignment=None):
        return workflow.validate_entries(assignment or self.assignment,
                                         self.response if response is None else response,
                                         self.assignment_hash)

    def write_json(self, name, value):
        path = workflow.translation_directory(create=True) / name
        with path.open("xb") as handle:
            handle.write(adapter.json_text(value).encode())
        return path

    def prepare_response(self):
        assignment = workflow.assign(self.context, "assignment.json")
        response = self.write_json("response.json", self.response)
        return assignment, response

    def publish_response(self, assignment, response, output="validated.json"):
        return workflow.validate_response(self.context, assignment, adapter.sha256(assignment.read_bytes()),
                                          response, adapter.sha256(response.read_bytes()), output)

    def test_assignment_provenance_indices_and_unchanged_inputs(self):
        output = workflow.assign(self.context, "assignment.json")
        self.assertEqual(output.parent, adapter.WORK_ROOT / "translation")
        self.assertEqual(output.read_bytes(), self.assignment_raw)
        self.assertEqual([r["entry_index"] for r in self.assignment["entries"]], [1, 2, 3, 4])
        self.assertEqual([r["id"] for r in self.assignment["entries"]], ["01", "2", "3", "4"])
        for field, raw in (("source", self.source_raw), ("snapshot", self.snapshot_raw), ("batch", self.batch_raw)):
            self.assertEqual(self.assignment[field]["sha256"], adapter.sha256(raw))
        self.assertEqual(self.source_path.read_bytes(), self.source_raw)
        self.assertEqual(self.snapshot_path.read_bytes(), self.snapshot_raw)
        self.assertEqual(self.batch_path.read_bytes(), self.batch_raw)
        self.assertEqual({p.name for p in self.run_directory.iterdir()}, {"batch-001.json"})
        self.assertEqual({p.name for p in output.parent.iterdir()}, {"assignment.json"})

    def test_validated_response_polish_nfd_uncertain_and_reordering(self):
        target = "ąćęłńóśźż ĄĆĘŁŃÓŚŹŻ a\u0328c\u0301 🙂"
        self.response["entries"][1].update(target=target, uncertain=True, notes="Sprawdzić kontekst.\nBez korekty.")
        self.response["entries"].reverse()
        assignment, response = self.prepare_response()
        before = response.read_bytes()
        output = self.publish_response(assignment, response)
        result = adapter.load_corpus(output)
        self.assertEqual(result["schema"], workflow.VALIDATED_SCHEMA)
        self.assertEqual(result["validation"], "structural-only")
        self.assertFalse(result["canonical"])
        self.assertFalse(result["build_input"])
        self.assertEqual(result["assignment"]["sha256"], self.assignment_hash)
        self.assertEqual(result["response"]["sha256"], adapter.sha256(before))
        self.assertEqual([r["entry_index"] for r in result["entries"]], [1, 2, 3, 4])
        self.assertEqual(result["entries"][1]["target"], target)
        self.assertTrue(result["entries"][1]["uncertain"])
        self.assertEqual(response.read_bytes(), before)
        self.assertEqual(self.batch_path.read_bytes(), self.batch_raw)
        # No new workflow artifact is a complete neutral build input.
        for value in (self.assignment, self.response, result):
            with self.assertRaises(adapter.AdapterError):
                adapter.build_bytes(self.source, value, ["english"])
        self.assertEqual(adapter.build_bytes(self.source, self.snapshot.bundle, ["english"]), self.source_raw)

    def test_exact_complete_response_and_identity(self):
        mutations = []
        for entries in ([], self.response["entries"][:-1], self.response["entries"] * 2,
                        [self.response["entries"][0]] * 4, {}, None):
            mutations.append({**self.response, "entries": entries})
        for key, value in (("entry_index", True), ("entry_index", 1.0), ("entry_index", "1"),
                           ("entry_index", 0), ("entry_index", -1), ("entry_index", []),
                           ("id", "1"), ("id", 1), ("value_sha256", "0" * 64),
                           ("target", None), ("target", 1), ("target", ""), ("target", " \t"),
                           ("uncertain", 0), ("uncertain", "false"), ("notes", None),
                           ("notes", []), ("extra", "unexpected")):
            changed = deepcopy(self.response)
            changed["entries"][0][key] = value
            mutations.append(changed)
        for key in workflow.RESPONSE_FIELDS:
            changed = deepcopy(self.response)
            del changed["entries"][0][key]
            mutations.append(changed)
        for key, value in (("schema", workflow.VALIDATED_SCHEMA), ("assignment_sha256", "0" * 64),
                           ("extra", False)):
            mutations.append({**self.response, key: value})
        for changed in mutations:
            with self.subTest(response=changed), self.assertRaises(adapter.AdapterError):
                self.check_response(changed)

    def test_token_sequence_not_set_and_no_unknown_syntax(self):
        original = self.response["entries"][0]["target"]
        for target in (original.replace("{0}", "{1}"), original.replace("%s", "%d"),
                       original.replace("[wave]", ""), original + "{0}",
                       original.replace("[wave]{0}", "{0}[wave]"),
                       original.replace("\\n", "\\t"), original + "[new]", original + "<",
                       original + "#", original + '"', original + ";", "'" + original):
            changed = deepcopy(self.response)
            changed["entries"][0]["target"] = target
            with self.subTest(target=target), self.assertRaises(adapter.AdapterError):
                self.check_response(changed)
        self.response["entries"][0]["target"] = "Cześć [wave] słowo {0} %s \\n[/wave]"
        self.check_response()

    def test_disallowed_characters_and_boundary_spaces(self):
        for suffix in ("\r", "\n", "\x00", "\x01", "\x7f", "\u202e", "\u200b",
                       "\ufeff", "\ue000", "\u0378", "\ud800", "\ufffd", "\u2028", "\u2029", "\t"):
            changed = deepcopy(self.response)
            changed["entries"][1]["target"] = "Tekst" + suffix
            with self.subTest(suffix=repr(suffix)), self.assertRaises(adapter.AdapterError):
                self.check_response(changed)
        for target in ("Spaced", "  Spaced ", " Spaced  "):
            changed = deepcopy(self.response)
            changed["entries"][2]["target"] = target
            with self.assertRaises(adapter.AdapterError):
                self.check_response(changed)

    def test_encoding_strict_round_trip_without_normalization(self):
        for encoding in adapter.ENCODINGS:
            assignment = {**self.assignment, "encoding": encoding}
            self.response["entries"][1]["target"] = "ąćęłńóśźż ĄĆĘŁŃÓŚŹŻ"
            with self.subTest(encoding=encoding):
                if encoding in ("cp1251", "cp1252"):
                    with self.assertRaises(adapter.AdapterError):
                        self.check_response(assignment=assignment)
                else:
                    self.assertEqual(self.check_response(assignment=assignment)[1]["target"],
                                     self.response["entries"][1]["target"])
        self.response["entries"][1]["target"] = "a\u0328"
        with self.assertRaises(adapter.AdapterError):
            self.check_response(assignment={**self.assignment, "encoding": "cp1250"})

    def test_changed_batch_rejected_even_with_new_hash(self):
        for field, value in (("entry_indices", [1]), ("source", {}), ("snapshot", {}),
                             ("canonical", True), ("build_input", True), ("batch_number", 2),
                             ("entries", [])):
            changed = deepcopy(self.context.batch.value)
            changed[field] = value
            raw = adapter.json_text(changed).encode()
            self.batch_path.write_bytes(raw)
            with self.subTest(field=field), self.assertRaises(adapter.AdapterError):
                self.load_context(batch_hash=adapter.sha256(raw))
        # A record excluded by the established quarantine cannot be assigned.
        changed = deepcopy(self.context.batch.value)
        changed["entry_indices"] = [5]
        changed["entries"] = [self.snapshot.bundle["entries"][5]]
        raw = adapter.json_text(changed).encode()
        self.batch_path.write_bytes(raw)
        with self.assertRaises(adapter.AdapterError):
            self.load_context(batch_hash=adapter.sha256(raw))

    def test_changed_snapshot_rejected_even_with_new_hash(self):
        changed = deepcopy(self.snapshot.bundle)
        changed["entries"][1]["target"] = "Hello"
        raw = adapter.json_text(changed).encode()
        self.snapshot_path.write_bytes(raw)
        with self.assertRaises(adapter.AdapterError):
            self.load_context(snapshot_hash=adapter.sha256(raw))

    def test_assignment_tampering_rejected_with_recomputed_hash(self):
        assignment, response = self.prepare_response()
        for field, value in (("batch", {}), ("source", {}), ("snapshot", {}),
                             ("entries", self.assignment["entries"][:-1]), ("encoding", "cp1250"),
                             ("canonical", 0), ("token_policy", "forged")):
            changed = {**self.assignment, field: value}
            assignment.write_bytes(adapter.json_text(changed).encode())
            with self.subTest(field=field), self.assertRaises(adapter.AdapterError):
                self.publish_response(assignment, response)
        self.assertFalse((assignment.parent / "validated.json").exists())

    def test_explicit_hashes_and_strict_json(self):
        for hashes in ({"batch_hash": "0" * 64}, {"snapshot_hash": "0" * 64},
                       {"source_hash": "0" * 64}, {"batch_hash": "bad"}):
            with self.assertRaises(adapter.AdapterError):
                self.load_context(**hashes)
        root = workflow.translation_directory(create=True)
        path = root / "invalid.json"
        for raw in (b'[]', b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}',
                    b'\xff', b'\xef\xbb\xbf{}', b'{'):
            path.write_bytes(raw)
            with self.subTest(raw=raw), self.assertRaises(adapter.AdapterError):
                workflow.load_json(path, adapter.sha256(raw))
        assignment, response = self.prepare_response()
        with self.assertRaises(adapter.AdapterError):
            workflow.validate_response(self.context, assignment, "0" * 64,
                                       response, adapter.sha256(response.read_bytes()), "no.json")
        with self.assertRaises(adapter.AdapterError):
            workflow.validate_response(self.context, assignment, self.assignment_hash,
                                       response, "0" * 64, "no.json")

    def test_input_and_output_paths(self):
        for name in ("../escape.json", "..\\escape.json", "x:ads.json", "NUL.json", "COM1.json",
                     "a..b.json", "end.json.", "data.win", "sub/result.json"):
            with self.subTest(name=name), self.assertRaises(adapter.AdapterError):
                workflow.assign(self.context, name)
        root = workflow.translation_directory(create=True)
        for path in (Path("batch-001.json"), self.run_directory / ".." / "synthetic-001" / self.batch_path.name,
                     self.batch_path, self.snapshot_path):
            with self.subTest(path=path), self.assertRaises(adapter.AdapterError):
                workflow.load_json(path, adapter.sha256(self.batch_raw))
        outside = root / "batch-001.json"
        outside.write_bytes(self.batch_raw)
        with self.assertRaises(adapter.AdapterError):
            workflow.load_json(outside, adapter.sha256(self.batch_raw), batch=True)

    def test_no_clobber_assignment_response_and_validated(self):
        assignment, response = self.prepare_response()
        validated = self.publish_response(assignment, response)
        for path in (assignment, response, validated):
            before = path.read_bytes()
            with self.assertRaises(adapter.AdapterError):
                workflow.assign(self.context, path.name)
            with self.assertRaises(adapter.AdapterError):
                self.publish_response(assignment, response, path.name)
            self.assertEqual(path.read_bytes(), before)

    def test_each_input_rechecked_immediately_before_publication(self):
        assignment, response = self.prepare_response()
        for path in (self.source_path, self.snapshot_path, self.batch_path, assignment, response):
            before = path.read_bytes()
            with self.subTest(path=path.name):
                try:
                    with patch.object(adapter.os, "fsync", side_effect=lambda fd: path.write_bytes(before + b"\n")):
                        with self.assertRaises(adapter.AdapterError):
                            self.publish_response(assignment, response)
                    self.assertFalse((assignment.parent / "validated.json").exists())
                    self.assertEqual(list(assignment.parent.glob(".adapter-*.tmp")), [])
                finally:
                    path.write_bytes(before)

    def test_exclusive_publish_race_and_no_hardlink_fallback(self):
        root = workflow.translation_directory(create=True)
        real_link = adapter.os.link

        def racing_link(source, destination):
            with Path(destination).open("xb") as handle:
                handle.write(b"other writer")
            real_link(source, destination)

        with patch.object(adapter.os, "link", side_effect=racing_link):
            with self.assertRaises(FileExistsError):
                workflow.assign(self.context, "raced.json")
        self.assertEqual((root / "raced.json").read_bytes(), b"other writer")
        with patch.object(adapter.os, "link", side_effect=OSError("unsupported")):
            with self.assertRaises(OSError):
                workflow.assign(self.context, "unsupported.json")
        self.assertFalse((root / "unsupported.json").exists())
        self.assertEqual(list(root.glob(".adapter-*.tmp")), [])

    def test_symlink_input_and_output_refused(self):
        root = workflow.translation_directory(create=True)
        link = root / "link.json"
        try:
            link.symlink_to(self.batch_path)
        except (OSError, NotImplementedError):
            self.skipTest("Brak uprawnień/obsługi symlinków.")
        with self.assertRaises(adapter.AdapterError):
            workflow.load_json(link, adapter.sha256(self.batch_raw))
        with self.assertRaises(adapter.AdapterError):
            workflow.assign(self.context, link.name)

    def test_translation_directory_reparse_refused(self):
        # Model the Windows reparse attribute without requiring junction privileges.
        root = workflow.translation_directory(create=True)
        real_lstat = Path.lstat

        def lstat(path):
            result = real_lstat(path)
            if path == root:
                class Reparse:
                    st_mode = result.st_mode
                    st_file_attributes = 0x400
                return Reparse()
            return result

        with patch.object(Path, "lstat", lstat):
            with self.assertRaises(adapter.AdapterError):
                workflow.assign(self.context, "no.json")

    def test_size_limits(self):
        with patch.object(adapter, "MAX_CORPUS_BYTES", 10):
            with self.assertRaises(adapter.AdapterError):
                workflow.load_json(self.batch_path, adapter.sha256(self.batch_raw), batch=True)
            with self.assertRaises(adapter.AdapterError):
                workflow.assign(self.context, "large.json")

    def test_cli_only_two_commands_and_refusal_without_output(self):
        common = ["--source", str(self.source_path), "--expected-sha256", adapter.sha256(self.source_raw),
                  "--snapshot", str(self.snapshot_path), "--expected-snapshot-sha256", adapter.sha256(self.snapshot_raw),
                  "--batch", str(self.batch_path), "--expected-batch-sha256", adapter.sha256(self.batch_raw)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(workflow.main(["assign", *common, "--output", "cli.json"]), 0)
        info = json.loads(stdout.getvalue())
        self.assertFalse(info["installed"])
        self.assertEqual(info["sha256"], self.assignment_hash)
        self.response["entries"].pop()
        response = self.write_json("invalid-response.json", self.response)
        with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
            self.assertEqual(workflow.main([
                "validate-response", *common, "--assignment", info["output"],
                "--expected-assignment-sha256", info["sha256"], "--response", str(response),
                "--expected-response-sha256", adapter.sha256(response.read_bytes()), "--output", "no.json",
            ]), 2)
        self.assertFalse((response.parent / "no.json").exists())
        for command in ("review", "merge", "build"):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                workflow.make_cli().parse_args([command])


if __name__ == "__main__":
    unittest.main()
