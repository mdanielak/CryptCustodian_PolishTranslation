"""Offline english batch preparation; no build, merge, translation or installation.

Uses localization_adapter's neutral export and exclusive publication workflow.
See CORPUS_BATCHES.md. Python 3.10+, standard library only.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any, Sequence

import localization_adapter as adapter


SECTION = "english"
BATCH_SIZE = 100
BATCH_SCHEMA = "crypt-custodian-working-batch/v1"
INDEX_SCHEMA = "crypt-custodian-batch-index/v1"
EXCLUSIONS_SCHEMA = "crypt-custodian-batch-exclusions/v1"
COMPLETENESS_SCHEMA = "crypt-custodian-batch-completeness/v1"
POLICY = "exact-english-conservative-selection/v1"
ANOMALY_FIELDS = (
    "edit_blockers", "duplicate_sections", "case_collisions", "duplicate_ids",
    "orphan_lines", "unusual_lines", "empty_key_entries", "blocked_entries",
    "ids_across_sections",
)


def corpus_directory(*, create: bool = False) -> Path:
    if adapter.ADAPTER_ROOT.name != "CrypyCustodian_PolishTranslation":
        raise adapter.AdapterError("Nieprawidłowy katalog adaptera.")
    work = adapter.work_directory() if create else adapter.WORK_ROOT
    adapter.reject_links(work)
    if not work.is_dir() or work.resolve().parent != adapter.ADAPTER_ROOT.resolve():
        raise adapter.AdapterError("Niebezpieczny katalog work.")
    root = work / "corpus"
    adapter.reject_links(root)
    if create:
        root.mkdir(exist_ok=True)
    adapter.reject_links(root)
    if not root.is_dir() or root.resolve().parent != work.resolve():
        raise adapter.AdapterError("Niebezpieczny katalog work/corpus.")
    return root


def run_path(root: Path, name: str) -> Path:
    # Reuse the Windows device/ADS/traversal policy, including the length limit.
    adapter.validate_output_name(name + ".json", "json")
    if name.endswith((".", " ")):
        raise adapter.AdapterError("Nieprawidłowa nazwa katalogu partii.")
    result = root / name
    adapter.reject_links(result)
    return result


def validate_neutral(source: adapter.Source, bundle: dict[str, Any]) -> None:
    # Unlike build, even a source-identical non-null target is NOT neutral here.
    # json_text distinguishes true/1, false/0, and preserves Unicode code points.
    if adapter.json_text(bundle) != adapter.json_text(adapter.export_bundle(source)):
        raise adapter.AdapterError("Wymagany pełny, niezmieniony neutralny eksport źródła.")


@dataclass(frozen=True)
class Snapshot:
    path: Path
    raw: bytes
    bundle: dict[str, Any]

    def verify(self) -> None:
        if adapter.read_limited(self.path, adapter.MAX_CORPUS_BYTES) != self.raw:
            raise adapter.AdapterError("Neutralny snapshot zmienił się od odczytu.")

    def reference(self) -> dict[str, Any]:
        return {"path": str(self.path), "sha256": adapter.sha256(self.raw),
                "size_bytes": len(self.raw)}


def load_snapshot(path: Path, expected_hash: str, source: adapter.Source) -> Snapshot:
    digest = adapter.check_hash(expected_hash)
    if not path.is_absolute() or ".." in path.parts:
        raise adapter.AdapterError("Snapshot wymaga pełnej ścieżki bez '..'.")
    adapter.validate_output_name(path.name, "json")
    adapter.reject_links(path)
    root = corpus_directory()
    if path.resolve(strict=True).parent != root.resolve():
        raise adapter.AdapterError("Snapshot musi być plikiem bezpośrednio w work/corpus.")
    path = path.resolve(strict=True)
    raw = adapter.read_limited(path, adapter.MAX_CORPUS_BYTES)
    if adapter.sha256(raw) != digest:
        raise adapter.AdapterError("SHA-256 snapshotu niezgodny z oczekiwanym.")
    snapshot = Snapshot(path, raw, adapter.load_corpus(path))
    snapshot.verify()
    source.verify()
    validate_neutral(source, snapshot.bundle)
    return snapshot


def export_snapshot(source: adapter.Source, name: str) -> Path:
    """Same full export/serialization/limit/publish pipeline as adapter export."""
    adapter.validate_output_name(name, "json")
    payload = adapter.json_text(adapter.export_bundle(source)).encode("utf-8")
    if len(payload) > adapter.MAX_CORPUS_BYTES:
        raise adapter.AdapterError("Eksport przekroczył limit rozmiaru korpusu.")
    source.verify()
    return adapter.publish(name, payload, "json", source.verify,
                           corpus_directory(create=True))


def make_plan(source: adapter.Source, snapshot: Snapshot) -> dict[str, bytes]:
    """Pure deterministic projection; caller performs filesystem revalidation.

    Entry indices are zero-based in the FULL neutral entries array, not IDs.
    All entry fields (including target:null) remain byte-semantically faithful.
    """
    validate_neutral(source, snapshot.bundle)
    bundle = snapshot.bundle
    manifest = bundle["manifest"]
    headers = [s for s in manifest["sections"] if s["name"] == SECTION]
    if not headers:
        raise adapter.AdapterError("Brak dokładnej sekcji english; brak fallbacku casefold.")
    collisions = [names for names in manifest["case_collisions"]
                  if any(name.casefold() == SECTION.casefold() for name in names)]
    duplicates = {d["numeric_id"]: d for d in manifest["duplicate_ids"]
                  if d["section"] == SECTION}
    selected: list[int] = []
    accepted: list[int] = []
    excluded: list[dict[str, Any]] = []
    outside: list[int] = []
    for index, entry in enumerate(bundle["entries"]):
        if entry["section"] != SECTION:
            outside.append(index)
            continue
        selected.append(index)
        reasons: list[dict[str, Any]] = []
        if entry["edit_blockers"]:
            reasons.append({"code": "local-edit-blockers", "details": entry["edit_blockers"]})
        numeric_id = entry["id"].lstrip("0") or "0"
        if numeric_id in duplicates:
            reasons.append({"code": "duplicate-numeric-id", "details": duplicates[numeric_id]})
        if collisions:
            reasons.append({"code": "case-ambiguous-section", "details": collisions})
        if len(headers) > 1:
            reasons.append({"code": "repeated-section", "details": headers})
        if reasons:
            excluded.append({"entry_index": index, "entry": deepcopy(entry),
                             "reasons": deepcopy(reasons)})
        else:
            accepted.append(index)

    common = {"policy": POLICY, "section": SECTION, "canonical": False,
              "build_input": False, "source": deepcopy(manifest["source"]),
              "snapshot": snapshot.reference()}
    files: dict[str, bytes] = {}

    def add(name: str, value: dict[str, Any]) -> dict[str, Any]:
        payload = adapter.json_text(value).encode("utf-8")
        if len(payload) > adapter.MAX_CORPUS_BYTES:
            raise adapter.AdapterError("Artefakt przekroczył limit rozmiaru JSON.")
        files[name] = payload
        return {"file": name, "sha256": adapter.sha256(payload), "size_bytes": len(payload)}

    batches = []
    flattened: list[int] = []
    for start in range(0, len(accepted), BATCH_SIZE):
        indices = accepted[start:start + BATCH_SIZE]
        number = len(batches) + 1
        records = deepcopy([bundle["entries"][i] for i in indices])
        reference = add(f"batch-{number:03d}.json", {
            **common, "schema": BATCH_SCHEMA, "batch_number": number,
            "entry_indices": indices, "entries": records,
        })
        batches.append({**reference, "batch_number": number,
                        "count": len(indices), "entry_indices": indices})
        flattened.extend(indices)

    quarantined = [record["entry_index"] for record in excluded]
    checks = {
        "complete_selected_partition": sorted(accepted + quarantined) == selected,
        "disjoint_selected_partition": not (set(accepted) & set(quarantined)),
        "complete_full_partition": sorted(selected + outside) == list(range(len(bundle["entries"]))),
        "ordered_unique_batches": flattened == accepted == sorted(set(accepted)),
        "ordered_unique_quarantine": quarantined == sorted(set(quarantined)),
        "batch_sizes": all(b["count"] == BATCH_SIZE for b in batches[:-1])
                       and (not batches or 1 <= batches[-1]["count"] <= BATCH_SIZE),
    }
    if not all(checks.values()):
        raise adapter.AdapterError("Błąd kompletności/rozłączności planu.")
    counts = {"full_export": len(bundle["entries"]), "selected": len(selected),
              "accepted": len(accepted), "quarantined": len(excluded),
              "outside_section": len(outside), "batches": len(batches)}
    exclusions = add("exclusions.json", {
        **common, "schema": EXCLUSIONS_SCHEMA, "counts": counts,
        "quarantine": excluded,
        "global_anomalies": {key: deepcopy(manifest[key]) for key in ANOMALY_FIELDS},
        "global_blockers_still_apply_to_build": True,
    })
    completeness = add("completeness.json", {
        **common, "schema": COMPLETENESS_SCHEMA, "counts": counts, "checks": checks,
        "selected_entry_indices": selected, "accepted_entry_indices": accepted,
        "quarantined_entry_indices": quarantined, "outside_section_entry_indices": outside,
    })
    # Last file is the completion marker; it hashes every other run artifact.
    add("index.json", {**common, "schema": INDEX_SCHEMA, "status": "complete",
                       "batch_size": BATCH_SIZE, "counts": counts, "batches": batches,
                       "exclusions": exclusions, "completeness": completeness})
    return files


def verify_files(directory: Path, files: dict[str, bytes]) -> None:
    adapter.reject_links(directory)
    if not directory.is_dir():
        raise adapter.AdapterError("Brak katalogu partii.")
    if {p.name for p in directory.iterdir()} != set(files):
        raise adapter.AdapterError("Niekompletny lub nadmiarowy zestaw artefaktów.")
    for name, expected in files.items():
        if adapter.read_limited(directory / name, adapter.MAX_CORPUS_BYTES) != expected:
            raise adapter.AdapterError(f"Zmieniony artefakt: {name}")


def prepare(source: adapter.Source, snapshot: Snapshot, run: str, *, verify_only: bool = False) -> Path:
    root = corpus_directory()
    directory = run_path(root, run)

    def guard() -> None:
        source.verify()
        snapshot.verify()

    guard()
    files = make_plan(source, snapshot)
    if verify_only:
        verify_files(directory, files)
        guard()
        return directory / "index.json"
    # Exclusive directory reservation: no resume, overwrite, or cleanup of old runs.
    guard()
    directory.mkdir(exist_ok=False)
    for name, payload in files.items():
        if name == "index.json":
            verify_files(directory, {k: v for k, v in files.items() if k != name})
        adapter.publish(name, payload, "json", guard, directory)
    verify_files(directory, files)
    guard()
    return directory / "index.json"


def make_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(
        "Neutralny eksport i robocze partie english po 100. Tylko nowe pliki work/corpus; bez build."
    ))
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("export", "prepare", "verify"):
        sub = commands.add_parser(command)
        sub.add_argument("--source", type=Path, required=True)
        sub.add_argument("--expected-sha256", required=True)
        sub.add_argument("--encoding", choices=adapter.ENCODINGS)
        if command == "export":
            sub.add_argument("--output", required=True, help="Nowa nazwa .json w work/corpus.")
        else:
            sub.add_argument("--snapshot", type=Path, required=True)
            sub.add_argument("--expected-snapshot-sha256", required=True)
            sub.add_argument("--run", required=True,
                             help="Nowa prosta nazwa katalogu, np. english-001.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = make_cli().parse_args(argv)
    try:
        source = adapter.load_source(args.source, args.expected_sha256, args.encoding)
        if args.command == "export":
            output = export_snapshot(source, args.output)
        else:
            snapshot = load_snapshot(args.snapshot, args.expected_snapshot_sha256, source)
            output = prepare(source, snapshot, args.run, verify_only=args.command == "verify")
        payload = adapter.read_limited(output, adapter.MAX_CORPUS_BYTES)
        print(adapter.json_text({"output": str(output), "sha256": adapter.sha256(payload),
                                 "size_bytes": len(payload), "installed": False}), end="")
        return 0
    except (adapter.AdapterError, OSError, UnicodeError, ValueError, RecursionError) as exc:
        print(f"ODMOWA: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
