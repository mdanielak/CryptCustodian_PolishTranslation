"""New review tests only; synthetic I/O in temporary work/translation/tests-*.

Never reads or revises real translations, corrections or accepted artifacts.
"""

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
import review_translation_response as review
import translation_workflow as workflow


class ReviewTokenTests(unittest.TestCase):
    """Shared workflow/revise token regressions; no filesystem I/O."""

    def test_printf_tokens_preserve_historical_space_flag(self):
        tokens, problems = adapter.extract_tokens("100% completion")
        self.assertEqual(problems, [])
        self.assertEqual(tokens, [{"kind": "printf", "text": "% c",
                                   "start": 3, "end": 6, "known": True}])
        for whitespace in ("\t", "\r", "\n", "\u00a0", "\u2003"):
            for value in (f"100%{whitespace}completion", f"100%{whitespace}ukończenia",
                          f"%+{whitespace}02d"):
                with self.subTest(value=value):
                    tokens, _ = adapter.extract_tokens(value)
                    self.assertFalse(any(t["kind"] == "printf" for t in tokens))
        formats = ["%%", "%s", "%02d", "%2$s", "%-10s", "%+08.2f", "%#x", "%*.*f",
                   "% d", "%  d", "%+ 02d", "%2$ 08.2f"]
        formats += [f"%{conversion}" for conversion in "diuoxXfFeEgGaAcsp"]
        value = " ".join(formats)
        tokens, problems = adapter.extract_tokens(value)
        self.assertEqual(problems, [])
        self.assertEqual([(t["kind"], t["text"]) for t in tokens],
                         [("printf", text) for text in formats])
        for token in tokens:
            self.assertEqual(value[token["start"]:token["end"]], token["text"])

    def test_translation_and_revision_preserve_historical_printf_tokens(self):
        for suffix in ("", " %s %02d %% %2$s %-10s %+08.2f %#x %*.*f"):
            with self.subTest(suffix=suffix):
                source = "Reach 100% completion" + suffix
                target = "Osiągnij 100% celu" + suffix
                tokens, problems = adapter.extract_tokens(source)
                self.assertEqual(problems, [])
                original = {"entry_index": 0, "id": "1",
                            "value_sha256": adapter.sha256(source.encode("utf-8")),
                            "source": source, "tokens": tokens}
                assignment_value = {"encoding": "utf-8", "entries": [original]}
                assignment = workflow.JsonInput(Path("unused-assignment.json"),
                    adapter.json_text(assignment_value).encode("utf-8"), assignment_value)
                digest = adapter.sha256(assignment.raw)
                record = {key: original[key] for key in ("entry_index", "id", "value_sha256")}
                record.update(target=target, uncertain=False, notes="")
                response = {"schema": workflow.RESPONSE_SCHEMA,
                            "assignment_sha256": digest, "entries": [record]}
                self.assertEqual(workflow.validate_entries(assignment.value, response, digest), [record])
                validated_value = {"entries": [{**record, "target": source}]}
                validated = workflow.JsonInput(Path("unused-validated.json"),
                    adapter.json_text(validated_value).encode("utf-8"), validated_value)
                correction = {"entry_index": 0, "id": "1",
                              "old_target_sha256": adapter.sha256(source.encode("utf-8")),
                              "new_target": target}
                self.assertEqual(review.correct_entries(assignment, validated, [correction]), [record])
                self.assertEqual(validated.value["entries"][0]["target"], source)
                self.assertEqual(target.encode("utf-8").decode("utf-8"), target)
                bad_targets = [target.replace("% c", "%c"),
                               "Osiągnij 100% ukończenia gry" + suffix]
                if suffix:
                    bad_targets += [target.replace("%s", ""), target.replace("%02d", "%d"),
                                    target.replace("%%", "%"), target.replace("%s %02d", "%02d %s")]
                for bad in bad_targets:
                    with self.subTest(bad=bad):
                        with self.assertRaisesRegex(adapter.AdapterError, "Tokeny"):
                            workflow.validate_entries(assignment.value,
                                {**response, "entries": [{**record, "target": bad}]}, digest)
                        with self.assertRaisesRegex(adapter.AdapterError, "Tokeny"):
                            review.correct_entries(assignment, validated,
                                [{**correction, "new_target": bad}])


class ReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="tests-review-")
        self.addCleanup(temp.cleanup)
        home = Path(temp.name) / "CrypyCustodian_PolishTranslation"
        home.mkdir()
        (home / "work").mkdir()
        for attribute, value in (("ADAPTER_ROOT", home), ("WORK_ROOT", home / "work")):
            patcher = patch.object(adapter, attribute, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        count = 1327 if self._testMethodName.startswith("test_merge") else 3
        if self._testMethodName == "test_merge_wrong_total":
            count = 1326
        lines = [f"{2000 + i}=Text {i}" for i in range(count)]
        lines[0] = "2000=Hello [wave]{0} %s \\n[/wave]"
        lines[1] = "2001= Spaced "
        duplicate_id = "1270"
        if self._testMethodName == "test_merge_explicit_1270_even_when_not_quarantined":
            lines[2] = "01270=Forbidden"
            duplicate_id = "99000"
        self.source_path = adapter.WORK_ROOT / "translations.synthetic.ini"
        self.source_path.write_bytes(("[french]\n1=Outside\n[english]\n" + "\n".join(lines)
                                      + f"\n{duplicate_id}=Duplicate\n0{duplicate_id}=Duplicate\n99999=[unknown]Blocked\n").encode())
        self.source_hash = adapter.sha256(self.source_path.read_bytes())
        self.source = adapter.load_source(self.source_path, self.source_hash)
        self.snapshot_path = batches.export_snapshot(self.source, "neutral.json")
        self.snapshot_hash = adapter.sha256(self.snapshot_path.read_bytes())
        self.snapshot = batches.load_snapshot(self.snapshot_path, self.snapshot_hash, self.source)
        self.plan = batches.make_plan(self.source, self.snapshot)
        run = self.snapshot_path.parent / "synthetic-001"
        run.mkdir()
        self.parts = []
        for name, raw in self.plan.items():
            if not name.startswith("batch-"):
                continue
            batch_path = run / name
            batch_path.write_bytes(raw)
            batch = workflow.load_json(batch_path, adapter.sha256(raw), batch=True)
            context = workflow.Context(self.source, self.snapshot, batch)
            number = batch.value["batch_number"]
            assignment = workflow.assign(context, f"assignment-{number:03d}.json")
            assignment_value = adapter.load_corpus(assignment)
            response = self.write(f"response-{number:03d}.json", {
                "schema": workflow.RESPONSE_SCHEMA,
                "assignment_sha256": self.ref(assignment)["sha256"],
                "entries": [{"entry_index": e["entry_index"], "id": e["id"],
                             "value_sha256": e["value_sha256"], "target": e["source"],
                             "uncertain": True, "notes": "Do sprawdzenia"}
                            for e in assignment_value["entries"]],
            })
            validated = workflow.validate_response(
                context, assignment, self.ref(assignment)["sha256"], response,
                self.ref(response)["sha256"], f"validated-{number:03d}.json")
            self.parts.append({"batch": self.ref(batch_path), "assignment": self.ref(assignment),
                               "response": self.ref(validated)})
        self.root = workflow.translation_directory()
        self.first = adapter.load_corpus(Path(self.parts[0]["response"]["path"]))
        entry = self.first["entries"][0]
        self.correction = {"entry_index": entry["entry_index"], "id": entry["id"],
                           "old_target_sha256": adapter.sha256(entry["target"].encode("utf-8")),
                           "new_target": "Cześć [wave]{0} %s \\n[/wave]"}
        self.request = {"schema": review.CORRECTIONS_SCHEMA, "batch": self.parts[0]["batch"],
                        "assignment": self.parts[0]["assignment"], "validated": self.parts[0]["response"],
                        "corrections": [self.correction]}
        self.serial = 0

    def ref(self, path):
        raw = path.read_bytes()
        return {"path": str(path), "sha256": adapter.sha256(raw), "size_bytes": len(raw)}

    def write(self, name, value):
        path = workflow.translation_directory(create=True) / name
        with path.open("xb") as handle:
            handle.write(adapter.json_text(value).encode("utf-8"))
        return path

    def operation(self, command, value, output="result.json", hook=None):
        self.serial += 1
        path = self.write(f"manifest-{self.serial}.json", value)
        session = review.Review(self.source, self.snapshot)
        manifest = session.load(path, self.ref(path)["sha256"])
        if hook:
            hook(session, manifest)
        return getattr(session, command)(manifest, output)

    def merge_request(self):
        return {"schema": review.MERGE_SCHEMA, "parts": deepcopy(self.parts)}

    def test_revision_polish_round_trip_provenance_and_no_other_changes(self):
        before = {p: p.read_bytes() for p in adapter.WORK_ROOT.rglob("*") if p.is_file()}
        self.correction["new_target"] = "ąćęłńóśźż ĄĆĘŁŃÓŚŹŻ a\u0328 [wave]{0} %s \\n[/wave]"
        output = self.operation("revise", self.request)
        result = adapter.load_corpus(output)
        expected_entries = deepcopy(self.first["entries"])
        expected_entries[0]["target"] = self.correction["new_target"]
        self.assertEqual(result["entries"], expected_entries)
        self.assertEqual(result["schema"], review.REVISED_SCHEMA)
        for key in ("installed", "build_input", "canonical"):
            self.assertIs(result[key], False)
        self.assertEqual(result["validated"], self.parts[0]["response"])
        self.assertEqual(result["corrections"], self.ref(self.root / "manifest-1.json"))
        self.assertEqual(output.read_bytes().decode("utf-8").encode("utf-8"), output.read_bytes())
        for path, raw in before.items():
            self.assertEqual(path.read_bytes(), raw)
        with self.assertRaises(adapter.AdapterError):
            adapter.build_bytes(self.source, result, ["english"])

    def test_correction_missing_duplicate_bad_binding_and_hash(self):
        bad = [[], None, {}, [self.correction, self.correction]]
        for key in self.correction:
            changed = dict(self.correction)
            del changed[key]
            bad.append([changed])
        for key, value in (("entry_index", 999999), ("entry_index", True), ("entry_index", []),
                           ("id", "2001"), ("id", 2000), ("old_target_sha256", "0" * 64),
                           ("old_target_sha256", "bad"), ("old_target_sha256", None),
                           ("new_target", None), ("new_target", ""), ("extra", 1)):
            bad.append([{**self.correction, key: value}])
        for corrections in bad:
            with self.subTest(corrections=corrections), self.assertRaises(adapter.AdapterError):
                self.operation("revise", {**self.request, "corrections": corrections})
            self.assertFalse((self.root / "result.json").exists())

    def test_correction_tokens_boundary_spaces_and_forbidden_characters(self):
        target = self.correction["new_target"]
        for changed in (target.replace("{0}", "{1}"), target.replace("[wave]{0}", "{0}[wave]"),
                        target.replace("%s", ""), target + "{0}", " " + target, target + " ",
                        target + "\n", target + "\u200b", target + "[unknown]"):
            request = deepcopy(self.request)
            request["corrections"][0]["new_target"] = changed
            with self.subTest(target=repr(changed)), self.assertRaises(adapter.AdapterError):
                self.operation("revise", request)
        entry = self.first["entries"][1]
        for target in ("Spaced", "  Spaced ", " Spaced  "):
            request = deepcopy(self.request)
            request["corrections"] = [{"entry_index": entry["entry_index"], "id": entry["id"],
                                       "old_target_sha256": adapter.sha256(entry["target"].encode()),
                                       "new_target": target}]
            with self.assertRaises(adapter.AdapterError):
                self.operation("revise", request)

    def test_correction_encoding_is_strict_without_normalization(self):
        assignment = workflow.load_json(Path(self.parts[0]["assignment"]["path"]), self.parts[0]["assignment"]["sha256"])
        validated = workflow.load_json(Path(self.parts[0]["response"]["path"]), self.parts[0]["response"]["sha256"])
        assignment.value["encoding"] = "cp1250"
        correction = {**self.correction, "new_target": "Zażółć [wave]{0} %s \\n[/wave]"}
        self.assertEqual(review.correct_entries(assignment, validated, [correction])[0]["target"], correction["new_target"])
        correction["new_target"] = "a\u0328 [wave]{0} %s \\n[/wave]"
        with self.assertRaises(adapter.AdapterError):
            review.correct_entries(assignment, validated, [correction])

    def test_validated_and_assignment_forgery_with_recomputed_hash(self):
        for field, value in (("entries", []), ("batch", {}), ("source", {}), ("snapshot", {}),
                             ("assignment", {}), ("canonical", 0), ("build_input", True),
                             ("validation", "reviewed"), ("schema", workflow.RESPONSE_SCHEMA)):
            changed = {**self.first, field: value}
            self.serial += 1
            path = self.write(f"forged-{self.serial}.json", changed)
            with self.subTest(field=field), self.assertRaises(adapter.AdapterError):
                self.operation("revise", {**self.request, "validated": self.ref(path)})
        assignment = adapter.load_corpus(Path(self.parts[0]["assignment"]["path"]))
        assignment["entries"][0]["id"] = "forged"
        path = self.write("bad-assignment.json", assignment)
        with self.assertRaises(adapter.AdapterError):
            self.operation("revise", {**self.request, "assignment": self.ref(path)})

    def test_references_hash_size_paths_and_strict_json(self):
        for key, value in (("sha256", "0" * 64), ("size_bytes", 0), ("size_bytes", True),
                           ("path", "relative.json"), ("sha256", [])):
            request = deepcopy(self.request)
            request["validated"][key] = value
            with self.subTest(key=key), self.assertRaises(adapter.AdapterError):
                self.operation("revise", request)
        for name in ("../escape.json", "sub/result.json", "NUL.json", "data.win"):
            with self.assertRaises(adapter.AdapterError):
                self.operation("revise", self.request, name)
        session = review.Review(self.source, self.snapshot)
        for i, raw in enumerate((b'{"x":1,"x":2}', b'{"x":NaN}', b'[]', b'\xef\xbb\xbf{}')):
            path = self.root / f"invalid-{i}.json"
            path.write_bytes(raw)
            with self.assertRaises(adapter.AdapterError):
                session.load(path, adapter.sha256(raw))

    def test_revision_no_clobber_including_input(self):
        output = self.operation("revise", self.request)
        for path in (output, Path(self.parts[0]["response"]["path"])):
            before = path.read_bytes()
            with self.assertRaises(adapter.AdapterError):
                self.operation("revise", self.request, path.name)
            self.assertEqual(path.read_bytes(), before)

    def test_each_revision_input_rechecked_at_publication(self):
        paths = [self.source_path, self.snapshot_path, *(Path(self.parts[0][key]["path"])
                 for key in ("batch", "assignment", "response")), Path(self.first["response"]["path"])]
        for path in paths + [None]:
            def hook(session, manifest):
                selected = path or manifest.path
                before = selected.read_bytes()
                self.addCleanup(selected.write_bytes, before)
                patcher = patch.object(adapter.os, "fsync", side_effect=lambda fd: selected.write_bytes(before + b"\n"))
                with patcher, self.assertRaises(adapter.AdapterError):
                    session.revise(manifest, "no.json")
                selected.write_bytes(before)
                self.assertFalse((self.root / "no.json").exists())
                self.assertEqual(list(self.root.glob(".adapter-*.tmp")), [])
            self.operation("revise", self.request, f"ok-{self.serial}.json", hook)

    def test_publication_race_and_no_unsafe_fallback(self):
        real_link = adapter.os.link

        def racing_link(source, destination):
            with Path(destination).open("xb") as handle:
                handle.write(b"other writer")
            real_link(source, destination)

        with patch.object(adapter.os, "link", side_effect=racing_link), self.assertRaises(FileExistsError):
            self.operation("revise", self.request)
        self.assertEqual((self.root / "result.json").read_bytes(), b"other writer")
        with patch.object(adapter.os, "link", side_effect=OSError("unsupported")), self.assertRaises(OSError):
            self.operation("revise", self.request, "unsupported.json")
        self.assertFalse((self.root / "unsupported.json").exists())
        self.assertEqual(list(self.root.glob(".adapter-*.tmp")), [])

    def test_merge_mixed_complete_ordered_provenance_and_no_clobber(self):
        revised = self.operation("revise", self.request, "revised.json")
        manifest = self.merge_request()
        manifest["parts"][0]["response"] = self.ref(revised)
        manifest["parts"].reverse()
        before = {p: p.read_bytes() for p in adapter.WORK_ROOT.rglob("*") if p.is_file()}
        output = self.operation("merge", manifest)
        result = adapter.load_corpus(output)
        self.assertEqual(result["schema"], review.ACCEPTED_SCHEMA)
        self.assertEqual(len(result["entries"]), 1327)
        self.assertEqual([e["entry_index"] for e in result["entries"]], list(range(1, 1328)))
        self.assertNotIn("1270", {e["id"].lstrip("0") for e in result["entries"]})
        self.assertEqual(result["entries"][0]["target"], self.correction["new_target"])
        for key in ("installed", "build_input", "canonical"):
            self.assertIs(result[key], False)
        for part in manifest["parts"]:
            for reference in part.values():
                self.assertIn(reference, result["provenance"])
        self.assertIn(self.parts[0]["response"], result["provenance"])
        self.assertIn(self.ref(self.root / "manifest-1.json"), result["provenance"])
        for reference in result["provenance"]:
            self.assertEqual(reference, self.ref(Path(reference["path"])))
        self.assertEqual(result["source"]["sha256"], self.source_hash)
        self.assertEqual(result["snapshot"]["sha256"], self.snapshot_hash)
        for path, raw in before.items():
            self.assertEqual(path.read_bytes(), raw)
        raw = output.read_bytes()
        with self.assertRaises(adapter.AdapterError):
            self.operation("merge", manifest)
        self.assertEqual(output.read_bytes(), raw)
        with self.assertRaises(adapter.AdapterError):
            adapter.build_bytes(self.source, result, ["english"])

    def test_merge_all_validated_happy_path(self):
        result = adapter.load_corpus(self.operation("merge", self.merge_request()))
        self.assertEqual(len(result["entries"]), 1327)
        self.assertEqual(result["entries"][0], self.first["entries"][0])

    def test_merge_missing_duplicate_extra_and_wrong_binding(self):
        for parts in (self.parts[:-1], self.parts + self.parts[:1], self.parts[:-1] + self.parts[:1]):
            with self.assertRaises(adapter.AdapterError):
                self.operation("merge", {"schema": review.MERGE_SCHEMA, "parts": parts})
        for key in ("assignment", "response"):
            manifest = self.merge_request()
            manifest["parts"][0][key] = manifest["parts"][1][key]
            with self.assertRaises(adapter.AdapterError):
                self.operation("merge", manifest)
        self.assertFalse((self.root / "result.json").exists())

    def test_merge_wrong_total(self):
        with self.assertRaisesRegex(adapter.AdapterError, "1327"):
            self.operation("merge", self.merge_request())

    def test_merge_explicit_1270_even_when_not_quarantined(self):
        with self.assertRaisesRegex(adapter.AdapterError, "1270"):
            self.operation("merge", self.merge_request())

    def test_merge_quarantined_batch_tampering(self):
        path = Path(self.parts[0]["batch"]["path"])
        value = adapter.load_corpus(path)
        value["entry_indices"][0] = 1328
        value["entries"][0] = self.snapshot.bundle["entries"][1328]
        path.write_bytes(adapter.json_text(value).encode())
        manifest = self.merge_request()
        manifest["parts"][0]["batch"] = self.ref(path)
        with self.assertRaisesRegex(adapter.AdapterError, "projekcją"):
            self.operation("merge", manifest)

    def test_merge_forged_revised_and_missing_provenance(self):
        revised = self.operation("revise", self.request, "revised.json")
        value = adapter.load_corpus(revised)
        value["entries"][0]["target"] = "Inny [wave]{0} %s \\n[/wave]"
        forged = self.write("forged.json", value)
        manifest = self.merge_request()
        manifest["parts"][0]["response"] = self.ref(forged)
        with self.assertRaisesRegex(adapter.AdapterError, "revised response"):
            self.operation("merge", manifest)
        Path(value["corrections"]["path"]).unlink()
        manifest["parts"][0]["response"] = self.ref(revised)
        with self.assertRaises(OSError):
            self.operation("merge", manifest)

    def test_merge_all_inputs_guarded_including_revision_ancestry(self):
        revised = self.operation("revise", self.request, "revised.json")
        request = self.merge_request()
        request["parts"][0]["response"] = self.ref(revised)
        manifest_path = self.write("merge-manifest.json", request)
        session = review.Review(self.source, self.snapshot)
        manifest = session.load(manifest_path, self.ref(manifest_path)["sha256"])

        def check_guard(output, value, guard):
            paths = {self.source_path, self.snapshot_path, *(item.path for item in session.inputs)}
            self.assertIn(Path(self.first["response"]["path"]), paths)
            self.assertIn(self.root / "manifest-1.json", paths)
            for path in paths:
                before = path.read_bytes()
                try:
                    path.write_bytes(before + b"\n")
                    with self.subTest(path=path.name), self.assertRaises(adapter.AdapterError):
                        guard()
                finally:
                    path.write_bytes(before)
            guard()
            return self.root / output

        with patch.object(workflow, "publish_document", side_effect=check_guard):
            session.merge(manifest, "not-created.json")
        self.assertFalse((self.root / "not-created.json").exists())

    def test_cli_revision_success_and_merge_refusal(self):
        manifest = self.write("cli-manifest.json", self.request)
        common = ["--source", str(self.source_path), "--expected-sha256", self.source_hash,
                  "--snapshot", str(self.snapshot_path), "--expected-snapshot-sha256", self.snapshot_hash,
                  "--manifest", str(manifest), "--expected-manifest-sha256", self.ref(manifest)["sha256"],
                  "--output", "cli-result.json"]
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(review.main(["revise", *common]), 0)
            self.assertEqual(review.main(["revise", *common]), 2)
            self.assertEqual(review.main(["merge", *common]), 2)
        info = json.loads(stdout.getvalue())
        self.assertFalse(info["installed"])
        self.assertFalse(info["build_input"])
        self.assertEqual(info["sha256"], self.ref(self.root / "cli-result.json")["sha256"])
        for command in ("build", "import", "install"):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                review.make_cli().parse_args([command])


if __name__ == "__main__":
    unittest.main()
