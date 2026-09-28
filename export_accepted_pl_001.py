"""Pinned, offline accepted-pl-001 exporter. Never a general build bypass."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any, cast

import corpus_batches as batches
import localization_adapter as adapter
import review_translation_response as review
import translation_workflow as workflow


SOURCE_SHA = "09d193cf8ef131c4bf9127bab49aeb9bf4c5cea88113a32d2c88813bd0dd286f"
SNAPSHOT_SHA = "115836a064d26de60a07d434cda3fd172af641b731aacea99e189c04c3edc810"
ACCEPTED_SHA = "a015283850980a99270e49deb705c23c106fcc121f783d7b2648d90d695c0245"
POLISH = "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ"
# Local extension only: historical TOKEN_POLICY and snapshots remain untouched.
RELEASE_TOKEN = re.compile(adapter.TOKEN.pattern + r"|(?P<release_marker>[~@])")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise adapter.AdapterError(message)


@dataclass(frozen=True)
class Context:
    source: adapter.Source
    snapshot: batches.Snapshot
    accepted: workflow.JsonInput
    provenance: tuple[workflow.JsonInput, ...]
    indices: tuple[int, ...]

    def verify(self) -> None:
        self.source.verify()
        self.snapshot.verify()
        self.accepted.verify()
        for item in self.provenance:
            item.verify()


@dataclass(frozen=True)
class Paths:
    source: Path
    snapshot: Path
    accepted: Path
    output_root: Path


def arguments(argv: list[str] | None = None) -> Paths:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", action="store_true", required=True,
                        help="Publish a new no-clobber directory; never install.")
    defaults = {
        "source": "inputs/translations.ini",
        "snapshot": "work/corpus/neutral-001.json",
        "accepted": "work/translation/accepted-pl-001.json",
        "output_root": str(Path(tempfile.gettempdir()) / "crypt-custodian-export"),
    }
    for name, default in defaults.items():
        parser.add_argument("--" + name.replace("_", "-"), type=Path,
                            default=os.environ.get("CC_EXPORT_" + name.upper(), default))
    args = parser.parse_args(argv)
    return Paths(*(getattr(args, name).absolute() for name in defaults))


def load_context(paths: Paths) -> Context:
    source = adapter.load_source(paths.source, SOURCE_SHA)
    snapshot = batches.load_snapshot(paths.snapshot, SNAPSHOT_SHA, source)
    accepted = workflow.load_json(paths.accepted, ACCEPTED_SHA)
    value = accepted.value
    review.fields(value, {"schema", "canonical", "installed", "build_input", "validation",
                          "section", "token_policy", "encoding", "source", "snapshot",
                          "provenance", "entries"})
    review.same({k: v for k, v in value.items() if k not in {"entries", "provenance"}}, {
        "schema": review.ACCEPTED_SCHEMA, "canonical": False, "installed": False,
        "build_input": False, "validation": "structural-only", "section": "english",
        "token_policy": adapter.TOKEN_POLICY, "encoding": "utf-8",
        "source": snapshot.bundle["manifest"]["source"], "snapshot": snapshot.reference(),
    }, "accepted metadata")
    refs: Any = value["provenance"]
    require(type(refs) is list and bool(refs), "Brak provenance.")
    provenance = []
    seen = set()
    for ref in cast(list[dict[str, Any]], refs):
        review.fields(ref, {"path", "sha256", "size_bytes"})
        path = Path(ref["path"])
        require(path not in seen, "Powtórzona referencja provenance.")
        seen.add(path)
        item = workflow.load_json(path, ref["sha256"], batch=path.parent.parent == batches.corpus_directory())
        review.same(item.reference(), ref, "provenance")
        provenance.append(item)
    # The pinned accepted hash authenticates the entire provenance list and records;
    # every listed file is additionally read, hashed and guarded before publication.
    plan = batches.make_plan(source, snapshot)
    complete = json.loads(plan["completeness.json"])
    indices = tuple(complete["accepted_entry_indices"])
    require(len(indices) == 1327, "Plan nie zawiera 1327 rekordów.")
    quarantine = complete["quarantined_entry_indices"]
    require([snapshot.bundle["entries"][i]["id"] for i in quarantine] == ["1270"],
            "Nieoczekiwana kwarantanna.")
    manifest = snapshot.bundle["manifest"]
    require(len(manifest["empty_key_entries"]) == 31
            and len(manifest["duplicate_ids"]) == 21
            and all(len(d["lines"]) == 2 for d in manifest["duplicate_ids"]),
            "Nieoczekiwany zestaw anomalii.")
    result = Context(source, snapshot, accepted, tuple(provenance), indices)
    result.verify()
    return result


def token_signature(text: str) -> list[tuple[str, str]]:
    return [(m.lastgroup or "unknown", m.group()) for m in RELEASE_TOKEN.finditer(text)]


def validate_target(original: str, target: str, key: str) -> bytes:
    require(type(target) is str and bool(target.strip()), "Pusty/nietekstowy target.")
    require(not any(c in target for c in "\r\n\x00"), "Newline/NUL w target.")
    _, problems = adapter.extract_tokens(target)
    require(not problems, "Niedozwolony znak/składnia target.")
    # Deliberately explicit: historical extraction did not include this marker.
    if key == "1901":
        require(original.count("@") == 1 and target.count("@") == 1,
                "ID 1901 musi zachować dokładnie jeden @.")
    require(token_signature(original) == token_signature(target),
            "Zmienione tokeny/parametry/kolejność/spacje tokenów.")
    require(adapter.boundary_spaces(original) == adapter.boundary_spaces(target),
            "Zmienione brzegowe spacje.")
    encoded = target.encode("utf-8", errors="strict")
    require(encoded.decode("utf-8", errors="strict") == target, "Błąd UTF-8 round-trip.")
    return encoded


def map_records(context: Context, records: list[dict]) -> list[tuple[int, adapter.Item, bytes]]:
    doc = context.source.document
    require(adapter.sha256(doc.raw) == SOURCE_SHA, "Niezgodny hash źródła.")
    require(doc.format.encoding == "utf-8" and not doc.format.bom
            and all(line.eol == "\r\n" for line in doc.lines), "Wymagany UTF-8 bez BOM, CRLF.")
    require(type(records) is list and len(records) == 1327, "Wymagane dokładnie 1327 rekordów.")
    expected = set(context.indices)
    seen = set()
    mapped = []
    for record in records:
        review.fields(record, workflow.RESPONSE_FIELDS)
        index = record["entry_index"]
        require(type(index) is int and index in expected and index not in seen,
                "Obcy/powtórzony entry_index lub kwarantanna.")
        seen.add(index)
        item = doc.items[index]
        entry = context.snapshot.bundle["entries"][index]
        require(item.section == "english" and entry["section"] == "english"
                and (item.key.lstrip("0") or "0") != "1270", "Kwarantanna/obca sekcja.")
        review.same({k: entry[k] for k in ("id", "section", "section_line", "line", "source",
                                         "value_sha256", "value_byte_span")}, {
            "id": item.key, "section": item.section, "section_line": item.section_line,
            "line": item.line, "source": item.value,
            "value_sha256": adapter.sha256(doc.raw[item.start:item.end]),
            "value_byte_span": [item.start, item.end],
        }, "entry_index/source/section/span")
        require(record["id"] == item.key and record["value_sha256"] == entry["value_sha256"]
                and adapter.sha256(item.value.encode("utf-8")) == entry["value_sha256"],
                "Niezgodne id/source/value hash.")
        require(not entry["edit_blockers"] and type(record["uncertain"]) is bool
                and type(record["notes"]) is str, "Niepoprawny rekord accepted.")
        mapped.append((index, item, validate_target(item.value, record["target"], item.key)))
    require(seen == expected, "Niekompletny zbiór indeksów.")
    return sorted(mapped, key=lambda row: row[0])


def verify_result(original: bytes, output: bytes, changes: list[dict]) -> None:
    """Exact gap comparison + inverse reconstruction, offsets are [start,end)."""
    old_cursor = new_cursor = 0
    inverse = []
    for change in changes:
        start, end = change["source_value_span"]
        new_start, new_end = change["output_value_span"]
        require(old_cursor <= start <= end <= len(original)
                and new_cursor <= new_start <= new_end <= len(output), "Niepoprawny zakres.")
        gap = output[new_cursor:new_start]
        require(gap == original[old_cursor:start], "Zmiana poza dozwolonym zakresem.")
        require(adapter.sha256(original[start:end]) == change["source_value_sha256"]
                and adapter.sha256(output[new_start:new_end]) == change["target_value_sha256"],
                "Niezgodny hash zakresu.")
        inverse.extend((gap, original[start:end]))
        old_cursor, new_cursor = end, new_end
    require(output[new_cursor:] == original[old_cursor:], "Zmieniony suffix poza zakresem.")
    inverse.append(output[new_cursor:])
    require(b"".join(inverse) == original, "Inverse reconstruction nieidentyczny.")


def build_candidate(context: Context, records: list[dict]) -> tuple[bytes, dict]:
    mapped = map_records(context, records)
    doc = context.source.document
    chunks = []
    changes = []
    cursor = length = 0
    for index, item, encoded in mapped:
        if encoded == doc.raw[item.start:item.end]:
            continue
        gap = doc.raw[cursor:item.start]
        chunks.extend((gap, encoded))
        start = length + len(gap)
        length = start + len(encoded)
        changes.append({"entry_index": index, "id": item.key, "section": item.section,
                        "section_line": item.section_line, "line": item.line,
                        "source_value_span": [item.start, item.end],
                        "output_value_span": [start, length],
                        "source_value_sha256": adapter.sha256(doc.raw[item.start:item.end]),
                        "target_value_sha256": adapter.sha256(encoded)})
        cursor = item.end
    chunks.append(doc.raw[cursor:])
    output = b"".join(chunks)
    require(len(output) <= adapter.MAX_SOURCE_BYTES, "Wynik zbyt duży.")
    verify_result(doc.raw, output, changes)
    after = adapter.parse(output, "utf-8")
    wanted = {index: encoded.decode("utf-8") for index, _, encoded in mapped}
    require(len(after.items) == len(doc.items) and len(after.lines) == len(doc.lines),
            "Readback: zmiana struktury.")
    for index, (old, new) in enumerate(zip(doc.items, after.items)):
        require((old.line, old.section, old.section_line, old.key) ==
                (new.line, new.section, new.section_line, new.key)
                and new.value == wanted.get(index, old.value), "Readback: niezgodna wartość.")
    require(all(a.kind == b.kind and a.eol == b.eol for a, b in zip(doc.lines, after.lines)),
            "Readback: rodzaj wiersza/EOL.")
    counts = Counter("".join(encoded.decode("utf-8") for _, _, encoded in mapped))
    coverage = {char: counts[char] for char in POLISH}
    return output, {
        "schema": "crypt-custodian-accepted-pl-001-release/v1",
        "verdict": "release-candidate", "scope": "offline INI only; not a runtime/font approval",
        "installed": False, "game_launched": False, "normalization": "none",
        "encoding": "strict UTF-8 without BOM", "eol": "CRLF",
        "input": {"source": context.snapshot.bundle["manifest"]["source"],
                  "snapshot": context.snapshot.reference(), "accepted": context.accepted.reference(),
                  "provenance": [p.reference() for p in context.provenance]},
        "records": len(mapped), "changed_values": len(changes),
        "unchanged_accepted_values": len(mapped) - len(changes),
        "preserved": {"empty_keys": 31, "duplicate_pairs": 21, "quarantine_id": "1270",
                      "all_non_value_bytes": True, "all_foreign_sections": True},
        "checks": {"readback": True, "allowed_ranges": True, "inverse_reconstruction": True,
                   "strict_utf8_round_trip": True, "id_1901_at": True},
        "unicode_coverage": coverage,
        "uncertain_records": sum(r["uncertain"] for r in records),
        "offsets": "zero-based byte offsets; half-open [start,end); source/output coordinate spaces",
        "changes": changes,
        "output": {"sha256": adapter.sha256(output), "size_bytes": len(output)},
    }


def reserve_release(root: Path, source: Path) -> Path:
    require(root.is_absolute() and ".." not in root.parts, "Wymagana pełna ścieżka wyjścia bez '..'.")
    adapter.reject_links(root)
    require(root.parent.is_dir(), "Rodzic katalogu wyjściowego musi istnieć.")
    protected = (source.parent.resolve(), adapter.ADAPTER_ROOT.resolve())
    require(not any(root.resolve().is_relative_to(p) for p in protected),
            "Wyjście nie może być w katalogu źródła ani projektu.")
    root.mkdir(exist_ok=True)
    for number in range(1, 10000):
        path = root / ("release" if number == 1 else f"release-{number:03d}")
        adapter.reject_links(path)
        try:
            path.mkdir(exist_ok=False)
        except FileExistsError:
            continue
        return path
    raise adapter.AdapterError("Brak wolnego katalogu release.")


def export(paths: Paths) -> dict:
    context = load_context(paths)
    records = context.accepted.value["entries"]
    noop = [{**r, "target": context.source.document.items[r["entry_index"]].value} for r in records]
    no_op_bytes, no_op_report = build_candidate(context, noop)
    require(no_op_bytes == context.source.document.raw and not no_op_report["changes"],
            "No-op nieidentyczny bajtowo.")
    output, report = build_candidate(context, records)
    require(all(report["unicode_coverage"].values()), "Brak pełnego pokrycia 18 polskich liter.")
    report["checks"]["no_op_byte_identity"] = True
    context.verify()
    directory = reserve_release(paths.output_root, paths.source)
    path = adapter.publish("translations.ini", output, "ini", context.verify, directory)
    readback = adapter.read_limited(path, adapter.MAX_SOURCE_BYTES)
    require(readback == output, "Niezgodny readback pliku opublikowanego.")
    verify_result(context.source.document.raw, readback, report["changes"])
    context.verify()
    report["output"]["path"] = str(path)
    report["checks"]["published_readback"] = True
    report_path = adapter.publish("report.json", adapter.json_text(report).encode("utf-8"),
                                  "json", context.verify, directory)
    require(adapter.load_corpus(report_path) == report, "Niezgodny readback raportu.")
    context.verify()
    return {**report["output"], "report": str(report_path), "verdict": report["verdict"],
            "changed_values": report["changed_values"], "records": report["records"],
            "installed": False}


def main() -> int:
    paths = arguments()
    try:
        print(adapter.json_text(export(paths)), end="")
        return 0
    except (adapter.AdapterError, OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        print(f"ODMOWA (not-safe; brak kompletnego release bez report.json): {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
