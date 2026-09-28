"""Explicit offline corrections and complete acceptance; no build/import/install.

Python 3.10+, stdlib. Reuses the existing neutral selection and validation policy.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import sys
from typing import Any, Sequence

import corpus_batches as batches
import localization_adapter as adapter
import translation_workflow as workflow


CORRECTIONS_SCHEMA = "crypt-custodian-corrections/v1"
MERGE_SCHEMA = "crypt-custodian-acceptance-manifest/v1"
REVISED_SCHEMA = "crypt-custodian-revised-response/v1"
ACCEPTED_SCHEMA = "crypt-custodian-accepted-response/v1"


def fields(value: Any, expected: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise adapter.AdapterError("Nieprawidłowy komplet pól kontraktu.")


def same(actual: Any, expected: Any, label: str) -> None:
    # Unlike Python equality, distinguishes bool/int and preserves Unicode.
    if adapter.json_text(actual) != adapter.json_text(expected):
        raise adapter.AdapterError(f"Niezgodny binding/zawartość: {label}.")


def correct_entries(assignment: workflow.JsonInput, validated: workflow.JsonInput,
                    corrections: Any) -> list[dict[str, Any]]:
    entries = deepcopy(validated.value["entries"])
    by_index = {entry["entry_index"]: entry for entry in entries}
    if not isinstance(corrections, list) or not corrections or len(corrections) > len(entries):
        raise adapter.AdapterError("Wymagana jawna, niepusta lista korekt.")
    seen: set[int] = set()
    for correction in corrections:
        fields(correction, {"entry_index", "id", "old_target_sha256", "new_target"})
        index = correction["entry_index"]
        if type(index) is not int or index not in by_index or index in seen:
            raise adapter.AdapterError("Brak rekordu korekty lub powtórzony/obcy entry_index.")
        seen.add(index)
        entry = by_index[index]
        if correction["id"] != entry["id"]:
            raise adapter.AdapterError("Niezgodny binding id korekty.")
        digest = correction["old_target_sha256"]
        if not isinstance(digest, str) or adapter.check_hash(digest) != adapter.sha256(entry["target"].encode("utf-8")):
            raise adapter.AdapterError("Niezgodny old_target_sha256 korekty.")
        entry["target"] = correction["new_target"]
    # Rechecks identity, tokens in order, boundary whitespace and strict encoding
    # round-trip. No normalization, no changes to notes/uncertain or source hash.
    return workflow.validate_entries(assignment.value, {
        "schema": workflow.RESPONSE_SCHEMA,
        "assignment_sha256": adapter.sha256(assignment.raw), "entries": entries,
    }, adapter.sha256(assignment.raw))


class Review:
    def __init__(self, source: adapter.Source, snapshot: batches.Snapshot):
        self.source = source
        self.snapshot = snapshot
        self.plan = batches.make_plan(source, snapshot)
        self.inputs: list[workflow.JsonInput] = []

    def load(self, path: Path, digest: str, *, batch: bool = False) -> workflow.JsonInput:
        result = workflow.load_json(path, digest, batch=batch)
        self.inputs.append(result)
        return result

    def reference(self, value: Any, *, batch: bool = False) -> workflow.JsonInput:
        fields(value, {"path", "sha256", "size_bytes"})
        if (not isinstance(value["path"], str) or not isinstance(value["sha256"], str)
                or type(value["size_bytes"]) is not int or value["size_bytes"] < 0):
            raise adapter.AdapterError("Nieprawidłowa referencja wejścia.")
        result = self.load(Path(value["path"]), value["sha256"], batch=batch)
        same(value, result.reference(), "referencja wejścia")
        return result

    def verify(self) -> None:
        self.source.verify()
        self.snapshot.verify()
        for item in self.inputs:
            item.verify()

    def assignment(self, batch_ref: Any, assignment_ref: Any) -> tuple[workflow.Context, workflow.JsonInput]:
        batch = self.reference(batch_ref, batch=True)
        if (batch.value.get("schema") != batches.BATCH_SCHEMA
                or self.plan.get(batch.path.name) != batch.raw):
            raise adapter.AdapterError("Partia nie jest dokładną projekcją neutralnego snapshotu.")
        context = workflow.Context(self.source, self.snapshot, batch)
        assignment = self.reference(assignment_ref)
        same(assignment.value, workflow.assignment_document(context), "assignment")
        return context, assignment

    def validated(self, context: workflow.Context, assignment: workflow.JsonInput,
                  value: workflow.JsonInput) -> None:
        fields(value.value, {"schema", "canonical", "build_input", "validation", "token_policy",
                             "encoding", "source", "snapshot", "batch", "assignment", "response", "entries"})
        if value.value["schema"] != workflow.VALIDATED_SCHEMA:
            raise adapter.AdapterError("Wymagany validated response, nie surowy response.")
        response = self.reference(value.value["response"])
        expected = workflow.assignment_document(context)
        entries = workflow.validate_entries(expected, response.value, adapter.sha256(assignment.raw))
        same(value.value, {
            "schema": workflow.VALIDATED_SCHEMA, "canonical": False, "build_input": False,
            "validation": "structural-only", "token_policy": adapter.TOKEN_POLICY,
            "encoding": expected["encoding"], "source": expected["source"],
            "snapshot": expected["snapshot"], "batch": expected["batch"],
            "assignment": assignment.reference(), "response": response.reference(), "entries": entries,
        }, "validated response")

    def revision(self, manifest: workflow.JsonInput) -> dict[str, Any]:
        request = manifest.value
        fields(request, {"schema", "batch", "assignment", "validated", "corrections"})
        if request["schema"] != CORRECTIONS_SCHEMA:
            raise adapter.AdapterError("Nieprawidłowy schemat korekt.")
        context, assignment = self.assignment(request["batch"], request["assignment"])
        validated = self.reference(request["validated"])
        self.validated(context, assignment, validated)
        entries = correct_entries(assignment, validated, request["corrections"])
        return {**validated.value, "schema": REVISED_SCHEMA, "installed": False,
                "validated": validated.reference(), "corrections": manifest.reference(), "entries": entries}

    def revise(self, manifest: workflow.JsonInput, output: str) -> Path:
        return workflow.publish_document(output, self.revision(manifest), self.verify)

    def merge(self, manifest: workflow.JsonInput, output: str) -> Path:
        request = manifest.value
        fields(request, {"schema", "parts"})
        if (request["schema"] != MERGE_SCHEMA or not isinstance(request["parts"], list)
                or len(request["parts"]) != 14):
            raise adapter.AdapterError("Wymagany komplet 14 partii 001–014.")
        expected_names = {f"batch-{number:03d}.json" for number in range(1, 15)}
        if {name for name in self.plan if name.startswith("batch-")} != expected_names:
            raise adapter.AdapterError("Neutralny plan nie ma dokładnie partii 001–014.")
        parts: dict[int, list[dict[str, Any]]] = {}
        for part in request["parts"]:
            fields(part, {"batch", "assignment", "response"})
            context, assignment = self.assignment(part["batch"], part["assignment"])
            number = context.batch.value["batch_number"]
            if number in parts:
                raise adapter.AdapterError("Powtórzona partia.")
            response = self.reference(part["response"])
            if response.value.get("schema") == REVISED_SCHEMA:
                revision = self.reference(response.value.get("corrections"))
                same(revision.value.get("batch"), context.batch.reference(), "partia korekt")
                same(revision.value.get("assignment"), assignment.reference(), "assignment korekt")
                same(response.value, self.revision(revision), "revised response")
            else:
                self.validated(context, assignment, response)
            parts[number] = response.value["entries"]
        if set(parts) != set(range(1, 15)):
            raise adapter.AdapterError("Brak/obca partia.")
        entries = [entry for number in range(1, 15) for entry in parts[number]]
        indices = [entry["entry_index"] for entry in entries]
        # Every part has already been compared against the neutral selection,
        # which excludes ALL quarantine. Explicitly forbid numeric ID 1270 too.
        if (len(entries) != 1327 or indices != sorted(set(indices))
                or any((entry["id"].lstrip("0") or "0") == "1270" for entry in entries)):
            raise adapter.AdapterError("Wymagane 1327 unikalnych rekordów bez ID 1270/kwarantanny.")
        provenance = []
        for item in [manifest, *self.inputs]:
            if item.reference() not in provenance:
                provenance.append(item.reference())
        result = {
            "schema": ACCEPTED_SCHEMA, "canonical": False, "installed": False, "build_input": False,
            "validation": "structural-only", "section": batches.SECTION,
            "token_policy": adapter.TOKEN_POLICY, "encoding": self.source.document.format.encoding,
            "source": self.snapshot.bundle["manifest"]["source"], "snapshot": self.snapshot.reference(),
            "provenance": provenance, "entries": entries,
        }
        return workflow.publish_document(output, result, self.verify)


def make_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Jawne korekty/accepted offline; bez build/import/instalacji.")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("revise", "merge"):
        sub = commands.add_parser(command)
        sub.add_argument("--source", type=Path, required=True)
        sub.add_argument("--expected-sha256", required=True)
        sub.add_argument("--encoding", choices=adapter.ENCODINGS)
        sub.add_argument("--snapshot", type=Path, required=True)
        sub.add_argument("--expected-snapshot-sha256", required=True)
        sub.add_argument("--manifest", type=Path, required=True)
        sub.add_argument("--expected-manifest-sha256", required=True)
        sub.add_argument("--output", required=True, help="Nowa nazwa .json w work/translation.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = make_cli().parse_args(argv)
    try:
        source = adapter.load_source(args.source, args.expected_sha256, args.encoding)
        snapshot = batches.load_snapshot(args.snapshot, args.expected_snapshot_sha256, source)
        review = Review(source, snapshot)
        manifest = review.load(args.manifest, args.expected_manifest_sha256)
        output = getattr(review, args.command)(manifest, args.output)
        raw = adapter.read_limited(output, adapter.MAX_CORPUS_BYTES)
        print(adapter.json_text({"output": str(output), "sha256": adapter.sha256(raw),
                                 "size_bytes": len(raw), "installed": False, "build_input": False}), end="")
        return 0
    except (adapter.AdapterError, OSError, UnicodeError, ValueError, RecursionError) as exc:
        print(f"ODMOWA: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
