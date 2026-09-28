"""One existing batch -> assignment -> validated response; no review/merge/build.

Python 3.10+, stdlib only. All new artifacts stay in work/translation.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any, Callable, Sequence

import corpus_batches as batches
import localization_adapter as adapter


ASSIGNMENT_SCHEMA = "crypt-custodian-translation-assignment/v1"
RESPONSE_SCHEMA = "crypt-custodian-translator-response/v1"
VALIDATED_SCHEMA = "crypt-custodian-validated-response/v1"
RESPONSE_FIELDS = {"entry_index", "id", "value_sha256", "target", "uncertain", "notes"}


def translation_directory(*, create: bool = False) -> Path:
    # Do not create work/ or any parent: an existing batch is a prerequisite.
    work = adapter.WORK_ROOT
    adapter.reject_links(work)
    if (adapter.ADAPTER_ROOT.name != "CrypyCustodian_PolishTranslation"
            or not work.is_dir()
            or work.resolve().parent != adapter.ADAPTER_ROOT.resolve()):
        raise adapter.AdapterError("Niebezpieczny katalog work.")
    root = work / "translation"
    adapter.reject_links(root)
    if create:
        root.mkdir(exist_ok=True)
    adapter.reject_links(root)
    if not root.is_dir() or root.resolve().parent != work.resolve():
        raise adapter.AdapterError("Wymagany bezpieczny katalog work/translation.")
    return root


@dataclass(frozen=True)
class JsonInput:
    path: Path
    raw: bytes
    value: dict[str, Any]

    def verify(self) -> None:
        if adapter.read_limited(self.path, adapter.MAX_CORPUS_BYTES) != self.raw:
            raise adapter.AdapterError(f"Wejście zmieniło się od odczytu: {self.path.name}")

    def reference(self) -> dict[str, Any]:
        return {"path": str(self.path), "sha256": adapter.sha256(self.raw),
                "size_bytes": len(self.raw)}


def load_json(path: Path, expected_hash: str, *, batch: bool = False) -> JsonInput:
    digest = adapter.check_hash(expected_hash)
    if not path.is_absolute() or ".." in path.parts:
        raise adapter.AdapterError("JSON wymaga pełnej ścieżki bez '..'.")
    adapter.validate_output_name(path.name, "json")
    adapter.reject_links(path)
    if batch:
        root = batches.corpus_directory()
        batches.run_path(root, path.parent.name)
        if path.resolve(strict=True).parent.parent != root.resolve():
            raise adapter.AdapterError("Partia musi być w work/corpus/<run>/.")
    elif path.resolve(strict=True).parent != translation_directory().resolve():
        raise adapter.AdapterError("Assignment/response musi być bezpośrednio w work/translation.")
    path = path.resolve(strict=True)
    raw = adapter.read_limited(path, adapter.MAX_CORPUS_BYTES)
    if adapter.sha256(raw) != digest:
        raise adapter.AdapterError("SHA-256 JSON niezgodny z oczekiwanym.")
    result = JsonInput(path, raw, adapter.load_corpus(path))
    result.verify()
    return result


@dataclass(frozen=True)
class Context:
    source: adapter.Source
    snapshot: batches.Snapshot
    batch: JsonInput

    def verify(self) -> None:
        self.source.verify()
        self.snapshot.verify()
        self.batch.verify()


def load_context(source_path: Path, source_hash: str, snapshot_path: Path,
                 snapshot_hash: str, batch_path: Path, batch_hash: str,
                 encoding: str | None = None) -> Context:
    source = adapter.load_source(source_path, source_hash, encoding)
    snapshot = batches.load_snapshot(snapshot_path, snapshot_hash, source)
    batch = load_json(batch_path, batch_hash, batch=True)
    # Reuse the accepted selection/quarantine policy, not a second batch parser.
    # Reconstruct in memory only; no run/index/report is created or modified.
    plan = batches.make_plan(source, snapshot)
    if (batch.value.get("schema") != batches.BATCH_SCHEMA
            or plan.get(batch.path.name) != batch.raw):
        raise adapter.AdapterError("Partia nie jest dokładną projekcją neutralnego snapshotu.")
    context = Context(source, snapshot, batch)
    context.verify()
    return context


def assignment_document(context: Context) -> dict[str, Any]:
    batch = context.batch.value
    return {
        "schema": ASSIGNMENT_SCHEMA, "canonical": False, "build_input": False,
        "section": batches.SECTION, "token_policy": adapter.TOKEN_POLICY,
        "encoding": context.source.document.format.encoding,
        "source": batch["source"], "snapshot": context.snapshot.reference(),
        "batch": context.batch.reference(),
        "entries": [
            {"entry_index": index, **{key: entry[key] for key in
             ("id", "value_sha256", "source", "tokens")}}
            for index, entry in zip(batch["entry_indices"], batch["entries"])
        ],
    }


def publish_document(name: str, value: dict[str, Any], guard: Callable[[], None]) -> Path:
    adapter.validate_output_name(name, "json")
    payload = adapter.json_text(value).encode("utf-8")
    if len(payload) > adapter.MAX_CORPUS_BYTES:
        raise adapter.AdapterError("Wynik przekroczył limit rozmiaru JSON.")
    guard()
    root = translation_directory(create=True)

    def verify() -> None:
        translation_directory()
        guard()

    return adapter.publish(name, payload, "json", verify, root)


def assign(context: Context, output: str) -> Path:
    return publish_document(output, assignment_document(context), context.verify)


def validate_entries(assignment: dict[str, Any], response: dict[str, Any],
                     assignment_hash: str) -> list[dict[str, Any]]:
    if (set(response) != {"schema", "assignment_sha256", "entries"}
            or response["schema"] != RESPONSE_SCHEMA
            or response["assignment_sha256"] != assignment_hash):
        raise adapter.AdapterError("Nieprawidłowy kontrakt response lub hash assignmentu.")
    entries = response["entries"]
    expected = {entry["entry_index"]: entry for entry in assignment["entries"]}
    if not isinstance(entries, list) or len(entries) != len(expected):
        raise adapter.AdapterError("Response wymaga dokładnie kompletu rekordów assignmentu.")
    accepted: dict[int, dict[str, Any]] = {}
    for record in entries:
        if not isinstance(record, dict) or set(record) != RESPONSE_FIELDS:
            raise adapter.AdapterError("Nieprawidłowe pola rekordu response.")
        index = record["entry_index"]
        if type(index) is not int or index not in expected or index in accepted:
            raise adapter.AdapterError("Obcy/powtórzony entry_index albo nieprawidłowy typ.")
        original = expected[index]
        if (record["id"] != original["id"]
                or record["value_sha256"] != original["value_sha256"]):
            raise adapter.AdapterError(f"Niezgodne id/value_sha256: {index}.")
        target = record["target"]
        if not isinstance(target, str) or not target.strip():
            raise adapter.AdapterError(f"Wymagany niepusty tekst target: {index}.")
        if type(record["uncertain"]) is not bool or not isinstance(record["notes"], str):
            raise adapter.AdapterError("uncertain musi być bool, notes musi być tekstem.")
        tokens, problems = adapter.extract_tokens(target)
        if problems:
            raise adapter.AdapterError(f"Niedozwolony znak/składnia w target: {index}.")
        if [(t["kind"], t["text"]) for t in tokens] != [
            (t["kind"], t["text"]) for t in original["tokens"]
        ]:
            raise adapter.AdapterError(f"Tokeny muszą być zgodne 1:1 i w kolejności: {index}.")
        if adapter.boundary_spaces(target) != adapter.boundary_spaces(original["source"]):
            raise adapter.AdapterError(f"Zmieniono brzegowe odstępy: {index}.")
        encoding = assignment["encoding"]
        try:
            if target.encode(encoding, errors="strict").decode(encoding, errors="strict") != target:
                raise adapter.AdapterError(f"Niezgodny round-trip target: {index}.")
        except UnicodeError as exc:
            raise adapter.AdapterError(f"Target niereprezentowalny w kodowaniu źródła: {index}.") from exc
        accepted[index] = dict(record)
    # Order may differ in translator response; published entries follow assignment.
    return [accepted[index] for index in expected]


def validate_response(context: Context, assignment_path: Path, assignment_hash: str,
                      response_path: Path, response_hash: str, output: str) -> Path:
    assignment = load_json(assignment_path, assignment_hash)
    expected = assignment_document(context)
    if adapter.json_text(assignment.value) != adapter.json_text(expected):
        raise adapter.AdapterError("Assignment niezgodny z partią/źródłem/snapshotem.")
    response = load_json(response_path, response_hash)
    entries = validate_entries(expected, response.value, adapter.sha256(assignment.raw))

    def guard() -> None:
        context.verify()
        assignment.verify()
        response.verify()

    result = {
        "schema": VALIDATED_SCHEMA, "canonical": False, "build_input": False,
        "validation": "structural-only", "token_policy": adapter.TOKEN_POLICY,
        "encoding": expected["encoding"], "source": expected["source"],
        "snapshot": expected["snapshot"], "batch": expected["batch"],
        "assignment": assignment.reference(), "response": response.reference(),
        "entries": entries,
    }
    return publish_document(output, result, guard)


def make_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(
        "Jedna istniejąca partia -> assignment -> validated response. "
        "Tylko nowe JSON w work/translation; bez review/merge/build."
    ))
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("assign", "validate-response"):
        sub = commands.add_parser(command)
        sub.add_argument("--source", type=Path, required=True)
        sub.add_argument("--expected-sha256", required=True)
        sub.add_argument("--encoding", choices=adapter.ENCODINGS)
        sub.add_argument("--snapshot", type=Path, required=True)
        sub.add_argument("--expected-snapshot-sha256", required=True)
        sub.add_argument("--batch", type=Path, required=True)
        sub.add_argument("--expected-batch-sha256", required=True)
        sub.add_argument("--output", required=True, help="Nowa nazwa .json w work/translation.")
        if command == "validate-response":
            sub.add_argument("--assignment", type=Path, required=True)
            sub.add_argument("--expected-assignment-sha256", required=True)
            sub.add_argument("--response", type=Path, required=True)
            sub.add_argument("--expected-response-sha256", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = make_cli().parse_args(argv)
    try:
        context = load_context(args.source, args.expected_sha256, args.snapshot,
                               args.expected_snapshot_sha256, args.batch,
                               args.expected_batch_sha256, args.encoding)
        if args.command == "assign":
            output = assign(context, args.output)
        else:
            output = validate_response(context, args.assignment, args.expected_assignment_sha256,
                                       args.response, args.expected_response_sha256, args.output)
        payload = adapter.read_limited(output, adapter.MAX_CORPUS_BYTES)
        print(adapter.json_text({"output": str(output), "sha256": adapter.sha256(payload),
                                 "size_bytes": len(payload), "installed": False}), end="")
        return 0
    except (adapter.AdapterError, OSError, UnicodeError, ValueError, RecursionError) as exc:
        print(f"ODMOWA: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
