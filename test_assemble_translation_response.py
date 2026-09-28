"""Synthetic fixtures only, in a temporary directory; no game/real corpus I/O.

Not executed on import. Run only with explicit permission.
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

import assemble_translation_response as assemble
import localization_adapter as adapter
import translation_workflow as workflow


class AssembleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="assemble-tests-")
        self.addCleanup(temp.cleanup)
        home = Path(temp.name).resolve() / "CrypyCustodian_PolishTranslation"
        self.root = home / "work" / "translation"
        self.root.mkdir(parents=True)
        for name, value in (("ADAPTER_ROOT", home), ("WORK_ROOT", home / "work")):
            patcher = patch.object(adapter, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        ref = {"path": str(home / "synthetic.json"), "sha256": "a" * 64, "size_bytes": 42}
        self.assignment = {
            "schema": workflow.ASSIGNMENT_SCHEMA, "canonical": False, "build_input": False,
            "section": "english", "token_policy": adapter.TOKEN_POLICY, "encoding": "utf-8",
            "source": {**ref, "canonical": False}, "snapshot": dict(ref), "batch": dict(ref),
            "entries": [{"entry_index": i, "id": str(10 + i), "source": f"Source {i}",
                         "tokens": [], "value_sha256": adapter.sha256(f"Source {i}".encode())}
                        for i in range(3)],
        }
        self.records = [{"entry_index": e["entry_index"], "id": e["id"],
                         "value_sha256": e["value_sha256"], "target": "Zażółć gęślą jaźń",
                         "uncertain": False, "notes": ""} for e in self.assignment["entries"]]
        self.assignment_path = self.write_json("assignment.json", self.assignment)
        self.assignment_hash = adapter.sha256(self.assignment_path.read_bytes())
        self.parts = [self.write_json("part-a.json", self.records[1:]),
                      self.write_json("part-b.json", self.records[:1])]

    def write_json(self, name, value):
        path = self.root / name
        path.write_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"))
        return path

    def set_indices(self, indices):
        for entry, record, index in zip(self.assignment["entries"], self.records, indices):
            entry["entry_index"] = record["entry_index"] = index
        self.write_json(self.assignment_path.name, self.assignment)
        self.assignment_hash = adapter.sha256(self.assignment_path.read_bytes())
        self.write_json(self.parts[0].name, self.records[1:])
        self.write_json(self.parts[1].name, self.records[:1])

    def run_assemble(self, *, parts=None, output="assembled.json", assignment=None, digest=None):
        return assemble.assemble(self.assignment_path if assignment is None else assignment,
                                 self.assignment_hash if digest is None else digest,
                                 self.parts if parts is None else parts, output)

    def refused(self, **kwargs):
        with self.assertRaises((adapter.AdapterError, OSError)):
            self.run_assemble(**kwargs)
        self.assertFalse((self.root / "assembled.json").exists())
        self.assertEqual(list(self.root.glob(".adapter-*.tmp")), [])

    def test_success_polish_round_trip_order_and_only_explicit_parts(self):
        self.records[0].update(target="ąćęłńóśźż ĄĆĘŁŃÓŚŹŻ a\u0328c\u0301 🙂", uncertain=True,
                               notes="Sprawdzić.\nBez normalizacji.")
        self.write_json(self.parts[1].name, self.records[:1])
        self.write_json("part-unselected.json", [{"invalid": True}])
        before = {p: p.read_bytes() for p in self.root.iterdir()}
        output = self.run_assemble()
        raw = output.read_bytes()
        result = json.loads(raw.decode("utf-8"))
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(result, {"schema": workflow.RESPONSE_SCHEMA,
                                  "assignment_sha256": self.assignment_hash, "entries": self.records})
        self.assertEqual(output.parent, self.root)
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data)
        self.assertEqual(set(self.root.iterdir()), set(before) | {output})

    def test_global_indices_and_assignment_order(self):
        # Gaps are allowed: batch selection can quarantine global corpus indices.
        # The helper must also preserve assignment order rather than sort indices.
        for indices in ((100, 101, 102), (100, 104, 109), (109, 100, 104)):
            with self.subTest(indices=indices):
                self.set_indices(indices)
                result = json.loads(self.run_assemble(output=f"assembled-{indices[1]}.json").read_bytes())
                self.assertEqual(result, {"schema": workflow.RESPONSE_SCHEMA,
                                          "assignment_sha256": self.assignment_hash,
                                          "entries": self.records})
                self.assertEqual([e["entry_index"] for e in result["entries"]], list(indices))

    def test_global_indices_missing_extra_duplicate_and_binding_substitution(self):
        for indices in ((100, 101, 102), (100, 104, 109), (109, 100, 104)):
            self.set_indices(indices)
            mutations = [[], self.records[:-1], self.records * 2,
                         [self.records[0], self.records[0], self.records[2]]]
            for foreign in (0, 103):
                mutations.append([*self.records, {**self.records[0], "entry_index": foreign}])
                mutations.append([{**self.records[0], "entry_index": foreign}, *self.records[1:]])
            for keys in (("id",), ("value_sha256",), ("id", "value_sha256")):
                records = deepcopy(self.records)
                for key in keys:
                    records[0][key], records[1][key] = records[1][key], records[0][key]
                mutations.append(records)
            for records in mutations:
                with self.subTest(indices=indices, records=records):
                    self.refused(parts=[self.write_json("invalid.json", records)])
            with self.subTest(indices=indices, overlap=True):
                overlapping = self.write_json("overlap.json", self.records[:1])
                self.refused(parts=[*self.parts, overlapping])
            changed = deepcopy(self.assignment)
            changed["entries"][0]["entry_index"] = indices[1]
            self.write_json(self.assignment_path.name, changed)
            with self.subTest(indices=indices, duplicate_assignment=True):
                self.refused(digest=adapter.sha256(self.assignment_path.read_bytes()))

    def test_no_token_or_source_encoding_validation(self):
        self.assignment["encoding"] = "cp1252"
        self.assignment_path = self.write_json("assignment.json", self.assignment)
        self.assignment_hash = adapter.sha256(self.assignment_path.read_bytes())
        self.records[0]["target"] = "  Żółć [unknown]{9}\n"
        self.write_json(self.parts[1].name, self.records[:1])
        with patch.object(adapter, "extract_tokens", side_effect=AssertionError("must not run")), \
                patch.object(workflow, "validate_entries", side_effect=AssertionError("must not run")):
            result = json.loads(self.run_assemble().read_bytes())
        self.assertEqual(result["entries"], self.records)

    def test_missing_duplicate_and_extra_records(self):
        for records in ([], self.records[:-1], self.records * 2,
                        [self.records[0], self.records[0], self.records[2]],
                        [*self.records, {**self.records[0], "entry_index": 3}]):
            with self.subTest(records=records):
                part = self.write_json("invalid.json", records)
                self.refused(parts=[part])
        self.refused(parts=[])
        self.refused(parts=[*self.parts, self.parts[0]])
        overlapping = self.write_json("overlap.json", self.records[:1])
        self.refused(parts=[*self.parts, overlapping])

    def test_binding_types_and_exact_response_fields(self):
        for key, value in (("entry_index", True), ("entry_index", 0.0), ("entry_index", "0"),
                           ("entry_index", -1), ("entry_index", []), ("id", 10), ("id", "11"),
                           ("value_sha256", "0" * 64), ("value_sha256", None),
                           ("target", ""), ("target", " \t\n"), ("target", 1), ("target", None),
                           ("target", "\ud800"), ("uncertain", 0), ("uncertain", "false"),
                           ("notes", None), ("notes", []), ("extra", False)):
            records = deepcopy(self.records)
            records[0][key] = value
            # ASCII escapes allow testing invalid Unicode scalar values in JSON.
            path = self.root / "invalid.json"
            path.write_bytes(adapter.json_text(records).encode("utf-8"))
            with self.subTest(key=key, value=repr(value)):
                self.refused(parts=[path])
        for key in workflow.RESPONSE_FIELDS:
            records = deepcopy(self.records)
            del records[0][key]
            with self.subTest(missing=key):
                self.refused(parts=[self.write_json("invalid.json", records)])
        self.refused(parts=[self.write_json("invalid.json", [None, *self.records[1:]])])

    def test_assignment_schema_and_strict_types(self):
        mutations = [{**self.assignment, key: value} for key, value in (
            ("schema", "forged"), ("canonical", 0), ("build_input", 0), ("section", "polish"),
            ("encoding", []), ("token_policy", "forged"), ("entries", []), ("entries", {}),
            ("source", {}), ("extra", True))]
        for key in self.assignment:
            changed = deepcopy(self.assignment)
            del changed[key]
            mutations.append(changed)
        for key, value in (("entry_index", True), ("entry_index", 1), ("entry_index", -1),
                           ("entry_index", 0.0), ("entry_index", "0"), ("entry_index", []),
                           ("id", 10), ("value_sha256", "bad"), ("source", None),
                           ("tokens", {}), ("tokens", [{"kind": "tag"}]), ("extra", True)):
            changed = deepcopy(self.assignment)
            changed["entries"][0][key] = value
            mutations.append(changed)
        for key, value in (("canonical", 0), ("size_bytes", True), ("size_bytes", 0),
                           ("path", "relative.json"), ("sha256", 123)):
            changed = deepcopy(self.assignment)
            changed["source"][key] = value
            mutations.append(changed)
        for changed in mutations:
            raw = adapter.json_text(changed).encode("utf-8")
            self.assignment_path.write_bytes(raw)
            with self.subTest(assignment=changed):
                self.refused(digest=adapter.sha256(raw))

    def test_assignment_hash_substitution(self):
        for digest in ("bad", "0" * 64):
            self.refused(digest=digest)
        self.assignment_path.write_bytes(self.assignment_path.read_bytes() + b"\n")
        self.refused()

    def test_bom_invalid_utf8_duplicate_keys_and_nonstandard_json(self):
        for path in (self.assignment_path, self.parts[0]):
            original = path.read_bytes()
            for raw in (b"\xef\xbb\xbf" + original, b"\xff", b"{", b'{"x":1,"x":2}',
                        b'[{"entry_index":0,"entry_index":1}]', b"[NaN]", b"[Infinity]",
                        b"null", b"{}"):
                with self.subTest(path=path.name, raw=raw[:30]):
                    try:
                        path.write_bytes(raw)
                        digest = adapter.sha256(raw) if path == self.assignment_path else self.assignment_hash
                        self.refused(digest=digest)
                    finally:
                        path.write_bytes(original)

    def test_no_clobber_including_input_files(self):
        output = self.run_assemble()
        for path in (output, self.assignment_path, *self.parts):
            before = path.read_bytes()
            with self.subTest(path=path.name), self.assertRaises(adapter.AdapterError):
                self.run_assemble(output=path.name)
            self.assertEqual(path.read_bytes(), before)

    def test_input_mutation_before_publication(self):
        for path in (self.assignment_path, *self.parts):
            before = path.read_bytes()
            try:
                with self.subTest(path=path.name), patch.object(
                        adapter.os, "fsync", side_effect=lambda fd: path.write_bytes(before + b"\n")):
                    self.refused()
            finally:
                path.write_bytes(before)

    def test_publication_race_and_no_unsafe_fallback(self):
        real_link = adapter.os.link

        def racing_link(source, destination):
            with Path(destination).open("xb") as handle:
                handle.write(b"other writer")
            real_link(source, destination)

        with patch.object(adapter.os, "link", side_effect=racing_link):
            with self.assertRaises(FileExistsError):
                self.run_assemble(output="raced.json")
        self.assertEqual((self.root / "raced.json").read_bytes(), b"other writer")
        with patch.object(adapter.os, "link", side_effect=OSError("unsupported")):
            self.refused()

    def test_paths(self):
        for output in ("../escape.json", "..\\escape.json", "sub/output.json", "x:ads.json",
                       "NUL.json", "COM1.json", "a..b.json", "end.json.", "data.win",
                       str(self.root / "absolute.json")):
            with self.subTest(output=output):
                self.refused(output=output)
        outside = self.root.parent / "outside.json"
        nested = self.root / "nested"
        nested.mkdir()
        for original, argument in ((self.assignment_path, "assignment"), (self.parts[0], "parts")):
            outside.write_bytes(original.read_bytes())
            inside = nested / "inside.json"
            inside.write_bytes(original.read_bytes())
            for path in (Path(original.name), self.root / ".." / "translation" / original.name,
                         outside, inside, self.root / "missing.json", self.root / "*.json", nested):
                kwargs = {argument: [path] if argument == "parts" else path}
                with self.subTest(argument=argument, path=path):
                    self.refused(**kwargs)

    def test_symlink_paths(self):
        link = self.root / "link.json"
        try:
            link.symlink_to(self.parts[0])
        except (OSError, NotImplementedError):
            self.skipTest("Brak uprawnień/obsługi symlinków.")
        self.refused(parts=[link, self.parts[1]])
        self.refused(assignment=link)
        self.refused(output=link.name)

    def test_reparse_directory(self):
        real_lstat = Path.lstat

        def lstat(path):
            result = real_lstat(path)
            if path == self.root:
                class Reparse:
                    st_mode = result.st_mode
                    st_file_attributes = 0x400
                return Reparse()
            return result

        with patch.object(Path, "lstat", lstat):
            self.refused()

    def test_size_limit(self):
        with patch.object(adapter, "MAX_CORPUS_BYTES", 10):
            self.refused()
            with self.assertRaises(adapter.AdapterError):
                assemble.load_part(self.parts[0])

    def test_cli_success_refusal_and_required_options(self):
        args = ["--assignment", str(self.assignment_path), "--expected-assignment-sha256",
                self.assignment_hash, "--part", str(self.parts[0]), "--part", str(self.parts[1]),
                "--output", "cli.json"]
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(assemble.main(args), 0)
        info = json.loads(stdout.getvalue())
        self.assertFalse(info["installed"])
        self.assertEqual(info["sha256"], adapter.sha256((self.root / "cli.json").read_bytes()))
        with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
            self.assertEqual(assemble.main(args), 2)
        for option in ("--assignment", "--expected-assignment-sha256", "--part", "--output"):
            incomplete = [item for i in range(0, len(args), 2) if args[i] != option
                          for item in args[i:i + 2]]
            with self.subTest(option=option), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                assemble.make_cli().parse_args(incomplete)


if __name__ == "__main__":
    unittest.main()
