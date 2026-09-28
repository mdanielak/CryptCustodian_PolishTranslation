"""Assemble explicitly named JSON arrays into a NEW, unvalidated response.

Python 3.10+, stdlib only. Assignment and parts must be absolute, direct files
in work/translation; no globbing or discovery. Global entry indices and their
assignment order are preserved (no renumbering). UTF-8 without BOM throughout.
Token semantics, boundary spaces, source-encoding round-trip and provenance
against the original corpus remain the responsibility of validate-response.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence

import localization_adapter as adapter
import translation_workflow as workflow


def fields(value: Any, expected: set[str]) -> None:
    if type(value) is not dict or set(value) != expected:
        raise adapter.AdapterError("Nieprawidłowe pola obiektu JSON.")


def text(value: Any, *, nonempty: bool = False) -> None:
    if type(value) is not str or (nonempty and not value.strip()):
        raise adapter.AdapterError("Wymagany tekst" + (" niepusty." if nonempty else "."))
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise adapter.AdapterError("Tekst nie jest reprezentowalny w UTF-8.") from exc


def digest(value: Any) -> None:
    text(value)
    if adapter.check_hash(value) != value:
        raise adapter.AdapterError("Hash w dokumencie wymaga małych cyfr hex.")


def reference(value: Any, *, source: bool = False) -> None:
    fields(value, {"path", "sha256", "size_bytes"} | ({"canonical"} if source else set()))
    text(value["path"], nonempty=True)
    path = Path(value["path"])
    if not path.is_absolute() or ".." in path.parts or "\x00" in value["path"]:
        raise adapter.AdapterError("Referencja wymaga pełnej ścieżki bez '..'.")
    digest(value["sha256"])
    if type(value["size_bytes"]) is not int or value["size_bytes"] <= 0:
        raise adapter.AdapterError("Nieprawidłowy size_bytes referencji.")
    if source and value["canonical"] is not False:
        raise adapter.AdapterError("Źródło assignmentu musi być niekanoniczne.")
    # References are metadata only: never open the game, snapshot or batch.


def validate_assignment(value: Any) -> list[dict[str, Any]]:
    fields(value, {"schema", "canonical", "build_input", "section", "token_policy",
                   "encoding", "source", "snapshot", "batch", "entries"})
    if (value["schema"] != workflow.ASSIGNMENT_SCHEMA
            or value["canonical"] is not False or value["build_input"] is not False
            or value["section"] != workflow.batches.SECTION
            or value["token_policy"] != adapter.TOKEN_POLICY
            or type(value["encoding"]) is not str or value["encoding"] not in adapter.ENCODINGS):
        raise adapter.AdapterError("Nieprawidłowy kontrakt assignmentu.")
    for name in ("source", "snapshot", "batch"):
        reference(value[name], source=name == "source")
    entries = value["entries"]
    if type(entries) is not list or not entries:
        raise adapter.AdapterError("Assignment wymaga niepustej tablicy entries.")
    seen: set[int] = set()
    for entry in entries:
        fields(entry, {"entry_index", "id", "value_sha256", "source", "tokens"})
        index = entry["entry_index"]
        if type(index) is not int or index < 0 or index in seen:
            raise adapter.AdapterError("Assignment wymaga unikalnych nieujemnych indeksów całkowitych.")
        seen.add(index)
        text(entry["id"], nonempty=True)
        digest(entry["value_sha256"])
        text(entry["source"])
        if type(entry["tokens"]) is not list:
            raise adapter.AdapterError("tokens musi być tablicą.")
        for token in entry["tokens"]:
            # Schema only: deliberately no extraction or comparison with target.
            fields(token, {"kind", "text", "start", "end", "known"})
            text(token["kind"], nonempty=True)
            text(token["text"])
            if (type(token["start"]) is not int or type(token["end"]) is not int
                    or not 0 <= token["start"] <= token["end"]
                    or type(token["known"]) is not bool):
                raise adapter.AdapterError("Nieprawidłowe typy/pola tokenu assignmentu.")
    return entries


def load_part(path: Path) -> tuple[Path, bytes, list[Any]]:
    if not path.is_absolute() or ".." in path.parts:
        raise adapter.AdapterError("--part wymaga pełnej ścieżki bez '..'.")
    adapter.validate_output_name(path.name, "json")
    adapter.reject_links(path)
    if path.resolve(strict=True).parent != workflow.translation_directory().resolve():
        raise adapter.AdapterError("Część musi być bezpośrednio w work/translation.")
    path = path.resolve(strict=True)
    raw = adapter.read_limited(path, adapter.MAX_CORPUS_BYTES)
    try:
        value = json.loads(raw.decode("utf-8"),
                           object_pairs_hook=adapter.no_duplicate_json_keys,
                           parse_constant=adapter.reject_json_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise adapter.AdapterError("Część wymaga JSON UTF-8 bez BOM.") from exc
    if type(value) is not list:
        raise adapter.AdapterError("Część musi być tablicą obiektów response.")
    return path, raw, value


def assemble(assignment_path: Path, expected_assignment_sha256: str,
             parts: Sequence[Path], output: str) -> Path:
    adapter.validate_output_name(output, "json")
    assignment = workflow.load_json(assignment_path, expected_assignment_sha256)
    expected = {entry["entry_index"]: entry for entry in validate_assignment(assignment.value)}
    if not parts:
        raise adapter.AdapterError("Wymagana przynajmniej jedna jawna --part.")
    inputs: dict[Path, bytes] = {}
    accepted: dict[int, dict[str, Any]] = {}
    for part in parts:
        path, raw, records = load_part(part)
        if path in inputs or path == assignment.path:
            raise adapter.AdapterError("Powtórzona część albo część będąca assignmentem.")
        inputs[path] = raw
        for record in records:
            fields(record, workflow.RESPONSE_FIELDS)
            index = record["entry_index"]
            if type(index) is not int or index not in expected or index in accepted:
                raise adapter.AdapterError("Obcy/powtórzony entry_index albo nieprawidłowy typ.")
            text(record["id"], nonempty=True)
            digest(record["value_sha256"])
            if (record["id"] != expected[index]["id"]
                    or record["value_sha256"] != expected[index]["value_sha256"]):
                raise adapter.AdapterError(f"Niezgodne id/value_sha256: {index}.")
            text(record["target"], nonempty=True)
            text(record["notes"])
            if type(record["uncertain"]) is not bool:
                raise adapter.AdapterError("uncertain musi być bool.")
            accepted[index] = record
    if len(accepted) != len(expected):
        raise adapter.AdapterError("Brak pełnego kompletu rekordów assignmentu.")

    def guard() -> None:
        assignment.verify()
        for path, raw in inputs.items():
            if adapter.read_limited(path, adapter.MAX_CORPUS_BYTES) != raw:
                raise adapter.AdapterError(f"Część zmieniła się od odczytu: {path.name}")

    return workflow.publish_document(output, {
        "schema": workflow.RESPONSE_SCHEMA,
        "assignment_sha256": adapter.sha256(assignment.raw),
        "entries": [accepted[index] for index in expected],
    }, guard)


def make_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(
        "Złóż jawne części w nowy response; bez walidacji tokenów, review ani buildu."
    ))
    parser.add_argument("--assignment", type=Path, required=True,
                        help="Pełna ścieżka JSON bezpośrednio w work/translation.")
    parser.add_argument("--expected-assignment-sha256", required=True)
    parser.add_argument("--part", type=Path, action="append", required=True,
                        help="Pełna ścieżka tablicy JSON w work/translation; powtarzalne, bez globów.")
    parser.add_argument("--output", required=True, help="Nowa nazwa .json w work/translation.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = make_cli().parse_args(argv)
    try:
        output = assemble(args.assignment, args.expected_assignment_sha256, args.part, args.output)
        payload = adapter.read_limited(output, adapter.MAX_CORPUS_BYTES)
        print(adapter.json_text({"output": str(output), "sha256": adapter.sha256(payload),
                                 "size_bytes": len(payload), "installed": False}), end="")
        return 0
    except (adapter.AdapterError, OSError, UnicodeError, ValueError, RecursionError) as exc:
        print(f"ODMOWA: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
