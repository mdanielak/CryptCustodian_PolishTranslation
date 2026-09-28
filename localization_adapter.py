"""Conservative, offline translations*.ini adapter; Python 3.10+, stdlib only.

No discovery, game integration, encoding conversion or in-place writes.
See STANDALONE_ADAPTER.md for the format, safety boundary and limitations.
"""

from __future__ import annotations

import argparse
import codecs
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any, Callable, Sequence
import unicodedata


ADAPTER_ROOT = Path(__file__).absolute().parent
WORK_ROOT = ADAPTER_ROOT / "work"
SCHEMA = "crypt-custodian-neutral-corpus/v1"
TOKEN_POLICY = "conservative-lexical/v1"
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_CORPUS_BYTES = 256 * 1024 * 1024
ENCODINGS = ("utf-8", "utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be",
             "cp1250", "cp1251", "cp1252")
BOMS = ((codecs.BOM_UTF32_LE, "utf-32-le"),
        (codecs.BOM_UTF32_BE, "utf-32-be"),
        (codecs.BOM_UTF8, "utf-8"),
        (codecs.BOM_UTF16_LE, "utf-16-le"),
        (codecs.BOM_UTF16_BE, "utf-16-be"))
SECTION = re.compile(r"[ \t]*\[([^\[\]\r\n]+)\][ \t]*")
ENTRY = re.compile(r"[ \t]*([0-9]+)[ \t]*=")
PHYSICAL_LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n|$)")
KNOWN_TAG = re.compile(
    r"(?:/?(?:wave|rainbow|shake|red)|delay|pulse|c_[a-z_]+|"
    r"snd_text_[A-Za-z0-9_]+|\$?[0-9a-fA-F]{6}|"
    r"(?:char_name|change_skel|create_obj|follow_m|imposter_health|per_add|"
    r"s_movie_key|s_slot|s_topui|say_hello|scale|switch_song|zip),"
    r"[^\[\]\r\n]+|(?:fa_center|follow_m|imposter_health|s_movie_key|say_hello))"
)
TOKEN = re.compile(
    r"(?P<tag>\[[^\[\]\r\n]*\])"
    r"|(?P<brace>\{(?:[A-Za-z_][A-Za-z0-9_]*|[0-9]+)\})"
    r"|(?P<printf>%%|%(?:[0-9]+\$)?[-+#0 ]*(?:[0-9]+|\*)?"
    r"(?:\.(?:[0-9]+|\*))?[diuoxXfFeEgGaAcsp])"
    r"|(?P<escape>\\(?:[nrt\\\"']|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}))"
    r"|(?P<syntax>[$#;=\"\t])"
    r"|(?P<unknown>[\[\]{}\\<>])"
)


class AdapterError(ValueError):
    """Expected refusal: no result should be published."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_hash(value: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise AdapterError("Oczekiwany SHA-256 musi mieć dokładnie 64 cyfry hex.")
    return value.lower()


def json_text(value: Any) -> str:
    """ASCII JSON is portable even with a legacy Windows console code page."""
    return json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2,
                      allow_nan=False) + "\n"


def reject_links(path: Path) -> None:
    """Reject existing symlinks and Windows reparse points in the entire path."""
    for component in reversed((path, *path.parents)):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise AdapterError(f"Dowiązanie/reparse point jest niedozwolone: {component}")


def read_limited(path: Path, limit: int) -> bytes:
    reject_links(path)
    if not path.is_file():
        raise AdapterError(f"Nie jest zwykłym plikiem: {path}")
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise AdapterError(f"Przekroczony limit wejścia ({limit} bajtów): {path}")
    return data


@dataclass(frozen=True)
class Format:
    encoding: str
    bom: bytes
    evidence: str


def decode_source(raw: bytes, encoding: str | None = None) -> tuple[str, Format]:
    if encoding is not None and encoding not in ENCODINGS:
        raise AdapterError("Nieobsługiwane kodowanie; użyj wartości z --help.")
    bom, detected = next(((mark, codec) for mark, codec in BOMS
                          if raw.startswith(mark)), (b"", None))
    if detected is not None and encoding is not None and encoding != detected:
        raise AdapterError("Jawne kodowanie jest sprzeczne z BOM.")
    codec = detected or encoding or "utf-8"
    evidence = "BOM" if detected else (
        "explicit" if encoding else "strict UTF-8; not proof of runtime encoding"
    )
    payload = raw[len(bom):]
    try:
        text = payload.decode(codec, errors="strict")
        if text.encode(codec, errors="strict") != payload:
            raise AdapterError("Kodowanie nie daje identycznego round-trip.")
    except UnicodeError as exc:
        raise AdapterError("Błędne bajty/kodowanie; brak automatycznego fallbacku. "
                           "Ustal kodowanie i podaj --encoding.") from exc
    if "\x00" in text:
        raise AdapterError("NUL w tekście: możliwe UTF-16/32 bez BOM lub dane binarne. "
                           "Wymagane poprawne, jawne kodowanie.")
    return text, Format(codec, bom, evidence)


def extract_tokens(value: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Lexical protection, not a claim about the game's tag semantics."""
    tokens: list[dict[str, Any]] = []
    problems: list[str] = []
    for match in TOKEN.finditer(value):
        kind = match.lastgroup or "unknown"
        token = match.group()
        known = kind != "unknown" and (
            kind != "tag" or KNOWN_TAG.fullmatch(token[1:-1]) is not None
        )
        tokens.append({"kind": kind, "text": token, "start": match.start(),
                       "end": match.end(), "known": known})
        if not known:
            problems.append(f"unknown-token@{match.start()}:{token}")
        if kind == "syntax" and token in {'"', ";", "#"}:
            problems.append(f"ambiguous-inline-INI-syntax@{match.start()}:{token}")
    if value.lstrip(" \t").startswith("'"):
        problems.append("possible single-quoted INI value")
    if any(unicodedata.category(char).startswith("C") and char != "\t"
           for char in value):
        problems.append("control/format/surrogate/private-use/unassigned character")
    if "\ufffd" in value or "\u2028" in value or "\u2029" in value:
        problems.append("replacement character or Unicode line separator")
    return tokens, problems


@dataclass(frozen=True)
class Line:
    number: int
    start: int
    end: int
    body: str
    eol: str
    kind: str


@dataclass(frozen=True)
class Item:
    line: int
    section: str | None
    section_line: int | None
    key: str
    value: str
    start: int
    end: int


@dataclass(frozen=True)
class Document:
    raw: bytes
    format: Format
    lines: tuple[Line, ...]
    items: tuple[Item, ...]


def parse(raw: bytes, encoding: str | None = None) -> Document:
    """Keep all bytes; only numeric key values acquire editable byte spans."""
    text, fmt = decode_source(raw, encoding)
    lines: list[Line] = []
    items: list[Item] = []
    offset = len(fmt.bom)
    section: str | None = None
    section_line: int | None = None
    for match in PHYSICAL_LINE.finditer(text):
        full = match.group()
        if not full:
            continue
        eol = "\r\n" if full.endswith("\r\n") else (
            full[-1] if full.endswith(("\r", "\n")) else ""
        )
        body = full[:-len(eol)] if eol else full
        number = len(lines) + 1
        header = SECTION.fullmatch(body)
        entry = ENTRY.match(body)
        kind = "unusual"
        if re.fullmatch(r"[ \t]*=[ \t]*", body):
            # This is not a blank line: it is an entry with an empty, unsupported
            # key.  Keep it byte-for-byte but make the global refusal explicit.
            kind = "empty-key-entry"
        elif not body.strip(" \t"):
            kind = "blank"
        elif body.lstrip(" \t").startswith((";", "#")):
            kind = "comment"
        elif header and header[1] == header[1].strip() and header[1]:
            kind = "section"
            section, section_line = header[1], number
        elif entry:
            kind = "entry"
            prefix = body[:entry.end()].encode(fmt.encoding)
            start = offset + len(prefix)
            end = offset + len(body.encode(fmt.encoding))
            items.append(Item(number, section, section_line, entry[1],
                              body[entry.end():], start, end))
        end_offset = offset + len(full.encode(fmt.encoding))
        lines.append(Line(number, offset, end_offset, body, eol, kind))
        offset = end_offset
    doc = Document(raw, fmt, tuple(lines), tuple(items))
    reconstructed = fmt.bom + b"".join(
        (line.body + line.eol).encode(fmt.encoding) for line in lines
    )
    if reconstructed != raw or offset != len(raw):
        raise AdapterError("Wewnętrzny błąd round-trip parsera.")
    return doc


@dataclass(frozen=True)
class Source:
    path: Path
    expected_hash: str
    document: Document

    def verify(self) -> None:
        """Re-read, not merely stat(): called again immediately before publication."""
        current = read_limited(self.path, MAX_SOURCE_BYTES)
        if sha256(current) != self.expected_hash or current != self.document.raw:
            raise AdapterError("Źródło zmieniło się od odczytu; zapis odrzucony.")


def load_source(path: Path, expected_hash: str, encoding: str | None = None) -> Source:
    digest = check_hash(expected_hash)
    if not path.is_absolute() or ".." in path.parts:
        raise AdapterError("--source wymaga jawnej ścieżki bezwzględnej bez '..'.")
    if not re.fullmatch(r"translations[^/\\:]*\.ini", path.name, re.IGNORECASE):
        raise AdapterError("Źródło musi mieć nazwę translations*.ini.")
    reject_links(path)
    path = path.resolve(strict=True)
    raw = read_limited(path, MAX_SOURCE_BYTES)
    if sha256(raw) != digest:
        raise AdapterError("SHA-256 źródła nie jest zgodny z oczekiwanym.")
    return Source(path, digest, parse(raw, encoding))


def manifest(source: Source) -> dict[str, Any]:
    doc = source.document
    sections: list[dict[str, Any]] = []
    section_names: dict[str, list[int]] = defaultdict(list)
    keys: dict[tuple[str | None, str], list[int]] = defaultdict(list)
    ids: dict[str, set[str]] = defaultdict(set)
    unusual: list[dict[str, Any]] = []
    for line in doc.lines:
        if line.kind == "section":
            header = SECTION.fullmatch(line.body)
            assert header is not None
            section_names[header[1]].append(line.number)
            sections.append({"name": header[1], "line": line.number})
        elif line.kind == "unusual":
            unusual.append({"line": line.number, "text": line.body})
    for item in doc.items:
        # Numeric aliases (01/1) are ambiguous until loader semantics are known.
        normalized_id = item.key.lstrip("0") or "0"
        keys[(item.section, normalized_id)].append(item.line)
        if item.section is not None:
            ids[normalized_id].add(item.section)
    duplicate_values: dict[tuple[str | None, str], list[str]] = defaultdict(list)
    for item in doc.items:
        normalized_id = item.key.lstrip("0") or "0"
        duplicate_values[(item.section, normalized_id)].append(item.value)
    duplicates = [
        {"section": section, "numeric_id": key, "lines": numbers,
         "classification": ("identical" if len(set(duplicate_values[(section, key)])) == 1
                            else "conflicting")}
        for (section, key), numbers in keys.items() if len(numbers) > 1
    ]
    repeated_sections = [{"name": name, "lines": numbers}
                         for name, numbers in section_names.items() if len(numbers) > 1]
    case_names: dict[str, list[str]] = defaultdict(list)
    for name in section_names:
        case_names[name.casefold()].append(name)
    case_collisions = [names for names in case_names.values() if len(names) > 1]
    orphan_lines = [item.line for item in doc.items if item.section is None]
    blockers: list[str] = []
    empty_key_entries = [
        {"line": line.number, "text": line.body}
        for line in doc.lines if line.kind == "empty-key-entry"
    ]
    if unusual:
        blockers.append("unusual physical lines (possible unsupported/multiline syntax)")
    if empty_key_entries:
        blockers.append("empty-key entries (optional whitespace, '=', optional whitespace)")
    if duplicates:
        blockers.append("duplicate numeric IDs within a section, including leading-zero aliases")
    if repeated_sections or case_collisions:
        blockers.append("duplicate or case-ambiguous sections")
    if orphan_lines:
        blockers.append("entries outside a section")
    if any(unicodedata.category(c).startswith("C") for name in section_names for c in name):
        blockers.append("control/format characters in section names")
    eols = Counter({"CRLF": 0, "LF": 0, "CR": 0, "NONE": 0})
    eol_names = {"\r\n": "CRLF", "\n": "LF", "\r": "CR", "": "NONE"}
    for line in doc.lines:
        eols[eol_names[line.eol]] += 1
    token_counts: Counter[str] = Counter()
    blocked_entries: list[dict[str, Any]] = []
    for item in doc.items:
        tokens, problems = extract_tokens(item.value)
        token_counts.update(token["text"] for token in tokens)
        if problems:
            blocked_entries.append({"line": item.line, "reasons": problems})
    return {
        "source": {"path": str(source.path), "sha256": source.expected_hash,
                   "size_bytes": len(doc.raw), "canonical": False},
        "format": {"encoding": doc.format.encoding, "bom_hex": doc.format.bom.hex(),
                   "encoding_evidence": doc.format.evidence, "eol_counts": dict(eols),
                   "final_eol": bool(doc.lines and doc.lines[-1].eol),
                   "round_trip_identical": True},
        "token_policy": TOKEN_POLICY,
        "line_count": len(doc.lines), "entry_count": len(doc.items),
        "line_kinds": dict(Counter(line.kind for line in doc.lines)),
        "sections": sections, "duplicate_sections": repeated_sections,
        "id_inventory": [{"section": item.section, "section_line": item.section_line,
                          "id": item.key, "line": item.line} for item in doc.items],
        "case_collisions": case_collisions, "duplicate_ids": duplicates,
        "ids_across_sections": {key: sorted(names) for key, names in ids.items()
                                if len(names) > 1},
        "orphan_lines": orphan_lines, "unusual_lines": unusual,
        "empty_key_entries": empty_key_entries,
        "edit_blockers": blockers, "blocked_entries": blocked_entries,
        "token_counts": dict(token_counts),
    }


def export_bundle(source: Source) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for item in source.document.items:
        tokens, problems = extract_tokens(item.value)
        entries.append({
            "line": item.line, "section": item.section, "section_line": item.section_line,
            "id": item.key, "value_byte_span": [item.start, item.end],
            "source": item.value,
            "value_sha256": sha256(source.document.raw[item.start:item.end]),
            "tokens": tokens, "edit_blockers": problems, "target": None,
        })
    return {"schema": SCHEMA, "manifest": manifest(source), "entries": entries}


def no_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AdapterError(f"Powtórzony klucz JSON: {key}")
        result[key] = value
    return result


def reject_json_constant(value: str) -> Any:
    raise AdapterError(f"Niedozwolona stała JSON: {value}")


def load_corpus(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(read_limited(path, MAX_CORPUS_BYTES).decode("utf-8"),
                           object_pairs_hook=no_duplicate_json_keys,
                           parse_constant=reject_json_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise AdapterError("Nieprawidłowy korpus JSON UTF-8 bez BOM.") from exc
    if not isinstance(value, dict):
        raise AdapterError("Korpus musi być obiektem JSON.")
    return value


def boundary_spaces(value: str) -> tuple[str, str]:
    return (value[:len(value) - len(value.lstrip(" \t"))],
            value[len(value.rstrip(" \t")):])


def build_bytes(source: Source, corpus: dict[str, Any], sections: Sequence[str]) -> bytes:
    """Validate a complete snapshot; permit changes to target fields only."""
    expected = export_bundle(source)
    names = {section["name"] for section in expected["manifest"]["sections"]}
    if not sections or len(set(sections)) != len(sections) or not set(sections) <= names:
        raise AdapterError("Podaj istniejące, niepowtarzające się --section (jawny zakres).")
    if not isinstance(corpus, dict) or set(corpus) != set(expected):
        raise AdapterError("Niepoprawne pola główne korpusu.")
    entries = corpus.get("entries")
    if not isinstance(entries, list) or len(entries) != len(source.document.items):
        raise AdapterError("Niekompletny korpus lub nadmiarowe wpisy.")
    stripped: list[dict[str, Any]] = []
    targets: list[str | None] = []
    for record in entries:
        if not isinstance(record, dict) or "target" not in record:
            raise AdapterError("Niepoprawny wpis korpusu lub brak target.")
        target = record["target"]
        if target is not None and not isinstance(target, str):
            raise AdapterError("target musi być tekstem albo null.")
        targets.append(target)
        stripped.append({**record, "target": None})
    sanitized = {**corpus, "entries": stripped}
    # Serialization also distinguishes JSON booleans from numeric identifiers.
    if json_text(sanitized) != json_text(expected):
        raise AdapterError("Zmienione metadane, źródło, kolejność, ID lub nieaktualny korpus.")
    edits: list[tuple[Item, bytes, str]] = []
    doc = source.document
    for item, target in zip(doc.items, targets):
        if target is None or target == item.value:
            continue
        if expected["manifest"]["edit_blockers"]:
            raise AdapterError("Niejednoznaczna struktura: dozwolony wyłącznie round-trip bez zmian.")
        if item.section not in sections:
            raise AdapterError(f"Zmiana poza jawnym zakresem, wiersz {item.line}.")
        old_tokens, old_problems = extract_tokens(item.value)
        new_tokens, new_problems = extract_tokens(target)
        if old_problems or new_problems:
            raise AdapterError(f"Nieznana składnia lub niedozwolony znak, wiersz {item.line}.")
        if "\r" in target or "\n" in target:
            raise AdapterError(f"Wartość wielowierszowa jest niedozwolona, wiersz {item.line}.")
        if [(t["kind"], t["text"]) for t in old_tokens] != [
            (t["kind"], t["text"]) for t in new_tokens
        ]:
            raise AdapterError(f"Zmieniono tokeny, parametry lub kolejność, wiersz {item.line}.")
        if boundary_spaces(item.value) != boundary_spaces(target):
            raise AdapterError(f"Zmieniono brzegowe odstępy, wiersz {item.line}.")
        try:
            encoded = target.encode(doc.format.encoding, errors="strict")
            if encoded.decode(doc.format.encoding, errors="strict") != target:
                raise AdapterError("Niezgodny round-trip wartości.")
        except UnicodeError as exc:
            raise AdapterError(f"Tekst niereprezentowalny w kodowaniu źródła, wiersz {item.line}.") from exc
        edits.append((item, encoded, target))
    chunks: list[bytes] = []
    cursor = 0
    for item, encoded, _ in edits:
        if item.start < cursor:
            raise AdapterError("Nakładające się zakresy wartości.")
        chunks.extend((doc.raw[cursor:item.start], encoded))
        cursor = item.end
    chunks.append(doc.raw[cursor:])
    result = b"".join(chunks)
    if len(result) > MAX_SOURCE_BYTES:
        raise AdapterError("Wynik przekroczył limit rozmiaru źródła.")
    reparsed = parse(result, doc.format.encoding)
    wanted = {item.line: target for item, _, target in edits}
    if len(reparsed.lines) != len(doc.lines) or len(reparsed.items) != len(doc.items):
        raise AdapterError("Zmiana struktury w wyniku.")
    for before, after in zip(doc.items, reparsed.items):
        if (before.line, before.section, before.section_line, before.key) != (
            after.line, after.section, after.section_line, after.key
        ) or after.value != wanted.get(before.line, before.value):
            raise AdapterError("Weryfikacja wartości po rekonstrukcji nie powiodła się.")
    for before, after in zip(doc.lines, reparsed.lines):
        if before.kind != after.kind or before.eol != after.eol:
            raise AdapterError("Zmiana rodzaju wiersza lub EOL.")
        if before.number not in wanted and doc.raw[before.start:before.end] != result[after.start:after.end]:
            raise AdapterError("Zmiana bajtów poza dozwolonym zakresem.")
    if not edits and result != doc.raw:
        raise AdapterError("Nieidentyczny round-trip bez zmian.")
    return result


def work_directory() -> Path:
    """The output root is fixed relative to this script, never relative to cwd."""
    if ADAPTER_ROOT.name != "CrypyCustodian_PolishTranslation":
        raise AdapterError("Adapter musi pozostać w katalogu CrypyCustodian_PolishTranslation.")
    reject_links(WORK_ROOT)
    if not ADAPTER_ROOT.is_dir():
        raise AdapterError("Brak katalogu adaptera.")
    WORK_ROOT.mkdir(exist_ok=True)  # Never create any parent directory.
    reject_links(WORK_ROOT)
    if not WORK_ROOT.is_dir() or WORK_ROOT.resolve().parent != ADAPTER_ROOT.resolve():
        raise AdapterError("Niebezpieczny katalog roboczy.")
    return WORK_ROOT


def audit_directory() -> Path:
    root = work_directory()
    audit = root / "audit"
    reject_links(audit)
    audit.mkdir(exist_ok=True)
    reject_links(audit)
    if not audit.is_dir() or audit.resolve().parent != root.resolve():
        raise AdapterError("Unsafe audit report directory.")
    return audit


def validate_output_name(name: str, kind: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", name) or ".." in name:
        raise AdapterError("--output to sama bezpieczna nazwa pliku, nie ścieżka.")
    if name.endswith((".", " ")) or name.split(".")[0].upper() in {
        "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)),
        *(f"LPT{i}" for i in range(10)),
    }:
        raise AdapterError("Niedozwolona nazwa urządzenia/pliku Windows.")
    if kind == "json" and not name.lower().endswith(".json"):
        raise AdapterError("Eksport wymaga nazwy .json.")
    if kind == "ini" and not re.fullmatch(r"translations[A-Za-z0-9_.-]*\.ini", name, re.IGNORECASE):
        raise AdapterError("Wynik build wymaga nazwy translations*.ini.")
    if kind not in {"json", "ini"}:
        raise AdapterError("Niedozwolony typ wyniku.")


def publish(name: str, payload: bytes, kind: str, verify_source: Callable[[], None],
            root: Path | None = None) -> Path:
    """Publish a complete NEW file using an exclusive hard link, never replace.

    Requires a local filesystem supporting hard links. No unsafe fallback.
    Work directory and ancestors must not be renamed concurrently (see docs).
    """
    validate_output_name(name, kind)
    root = work_directory() if root is None else root
    reject_links(root)
    if not root.is_dir():
        raise AdapterError("Unsafe output directory.")
    output = root / name
    reject_links(output)
    if output.exists():
        raise AdapterError("Plik wynikowy już istnieje; nadpisywanie jest zabronione.")
    verify_source()
    descriptor, temporary_name = tempfile.mkstemp(prefix=".adapter-", suffix=".tmp", dir=root)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        reject_links(root)
        reject_links(output)
        verify_source()
        # Atomic visibility and no-clobber even if output appeared after exists().
        os.link(temporary, output)
    finally:
        temporary.unlink()
    return output


def make_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(
        "Bezstratny, samodzielny adapter INI. Brak instalacji i zapisu do gry. "
        "Wszystkie wyniki wyłącznie w work/ obok skryptu."
    ))
    commands = parser.add_subparsers(dest="command", required=True)
    for command, help_text in (
        ("inspect", "Raport JSON na stdout albo jako nowy plik w work/audit/."),
        ("export", "Nowy JSON: neutralny manifest i kompletny korpus z target=null."),
        ("build", "Walidacja i nowy INI w work/; nigdy instalacja."),
    ):
        sub = commands.add_parser(command, help=help_text)
        sub.add_argument("--source", type=Path, required=True)
        sub.add_argument("--expected-sha256", required=True)
        sub.add_argument("--encoding", choices=ENCODINGS)
        if command == "inspect":
            sub.add_argument("--output", help="Optional new .json name in work/audit only.")
        else:
            sub.add_argument("--output", required=True)
        if command == "build":
            sub.add_argument("--corpus", type=Path, required=True)
            sub.add_argument("--section", action="append", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = make_cli().parse_args(argv)
    try:
        source = load_source(args.source, args.expected_sha256, args.encoding)
        if args.command == "inspect":
            payload = json_text(manifest(source)).encode("utf-8")
            if args.output is None:
                source.verify()
                print(payload.decode("utf-8"), end="")
            else:
                publish(args.output, payload, "json", source.verify, audit_directory())
            return 0
        if args.command == "export":
            payload = json_text(export_bundle(source)).encode("utf-8")
            if len(payload) > MAX_CORPUS_BYTES:
                raise AdapterError("Eksport przekroczył limit rozmiaru korpusu.")
            kind = "json"
        else:
            payload = build_bytes(source, load_corpus(args.corpus), args.section)
            kind = "ini"
        output = publish(args.output, payload, kind, source.verify)
        print(json_text({"output": str(output), "sha256": sha256(payload),
                         "size_bytes": len(payload), "installed": False}), end="")
        return 0
    except (AdapterError, OSError, UnicodeError, ValueError, RecursionError) as exc:
        print(f"ODMOWA: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
