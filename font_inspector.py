"""Read-only, bounded GameMaker FONT/STRG/LANG inspector. Python 3.10+, stdlib only.

No game discovery, format guessing, package writer or texture decoding.
See FONT_INSPECTOR.md for the explicit version/LTS trust boundary.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import struct
import sys


ROOT = Path(__file__).absolute().parent
WORK = ROOT / "work"
POLISH = "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ"
MAX_SOURCE = 1024 * 1024 * 1024
MAX_CHUNKS = 4096
MAX_FONTS = 4096
MAX_TPAG = 65536
MAX_STRINGS = 1_000_000
MAX_STRING_BYTES = 65536
STRG_TERMS = ("translations.ini", "translations2.ini", "ini_open", "ini_read_string",
              "language", "english", "polish", "font")
MAX_STRG_HITS = 256
MAX_HIT_CHARS = 160
MAX_LANGUAGES = 64
MAX_LANG_ENTRIES = 16384
MAX_LANG_TRANSLATIONS = 65536
MAX_LANG_TEXT_JSON = 16 * 1024 * 1024
STRING_WARNING = ("Obecność stringa w STRG/LANG nie dowodzi użycia przez runtime, "
                  "ładowania pliku, wyboru języka ani fontu.")
MAX_GLYPHS = 65536
MAX_TOTAL_GLYPHS = 262144
MAX_TOTAL_KERNING = 1_000_000
MAX_REPORT = 128 * 1024 * 1024


class InspectorError(ValueError):
    """Fail closed; no successful report should be published."""


@dataclass(frozen=True)
class Region:
    start: int
    end: int
    label: str

    def check(self, offset: int, size: int) -> None:
        if size < 0 or offset < self.start or offset > self.end - size:
            raise InspectorError(f"{self.label}: poza granicami: {offset:#x} + {size}")


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.file = Region(0, len(data), "plik")

    def unpack(self, fmt: str, offset: int, region: Region):
        size = struct.calcsize("<" + fmt)
        self.file.check(offset, size)
        region.check(offset, size)
        return struct.unpack_from("<" + fmt, self.data, offset)

    def scalar(self, fmt: str, offset: int, region: Region):
        return self.unpack(fmt, offset, region)[0]

    def pointer_list(self, offset: int, region: Region, limit: int,
                     minimum: int) -> tuple[list[int], dict[int, Region]]:
        count = self.scalar("I", offset, region)
        if count > limit:
            raise InspectorError(f"{region.label}: count {count} przekracza limit {limit}")
        region.check(offset + 4, count * 4)
        pointers = list(self.unpack(f"{count}I", offset + 4, region))
        if len(set(pointers)) != count:
            raise InspectorError(f"{region.label}: powtórzone wskaźniki")
        table_end = offset + 4 + count * 4
        ordered = sorted(pointers)
        bounds = {}
        for i, pointer in enumerate(ordered):
            if pointer < table_end:
                raise InspectorError(f"{region.label}: wskaźnik NULL/do nagłówka/listy")
            end = ordered[i + 1] if i + 1 < count else region.end
            region.check(pointer, minimum)
            item = Region(pointer, end, f"{region.label} rekord {pointer:#x}")
            item.check(pointer, minimum)
            bounds[pointer] = item
        return pointers, bounds


def check_hash(value: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise InspectorError("SHA-256 musi zawierać dokładnie 64 cyfry hex")
    return value.lower()


def version_tuple(value: str) -> tuple[int, int, int, int]:
    if not re.fullmatch(r"[0-9]{1,10}(?:\.[0-9]{1,10}){3}", value):
        raise InspectorError("Wersja musi mieć postać Major.Minor.Release.Build")
    result = tuple(int(part) for part in value.split("."))
    if any(part > 0xFFFFFFFF for part in result):
        raise InspectorError("Składnik wersji przekracza u32")
    return result[0], result[1], result[2], result[3]


def version_fields(version: tuple[int, ...], bytecode: int,
                   lts: bool | None) -> list[tuple[str, str]]:
    # Deliberately cap the supported contract before UMT's 2024.14 alignment.
    if not (version[0] in (1, 2) or 2022 <= version[0] <= 2024):
        raise InspectorError("Nieobsługiwana rodzina wersji GameMaker")
    if version >= (2024, 14, 0, 0):
        raise InspectorError("Wersja >=2024.14 poza potwierdzonym kontraktem")
    if not 14 <= bytecode <= 17:
        raise InspectorError("Bytecode poza obsługiwanym zakresem 14..17")
    if version >= (2023, 2, 0, 0) and lts is None:
        raise InspectorError("Niepewne LTS: wymagane --lts i --lts-evidence; brak zgadywania")
    fields = []
    if bytecode >= 17:
        fields.append(("ascender_offset", "i"))
    if version >= (2022, 2, 0, 0):
        fields.append(("ascender", "I"))
    if version >= (2023, 2, 0, 0) and lts is False:
        fields.append(("sdf_spread", "I"))
    if version >= (2023, 6, 0, 0):
        fields.append(("line_height", "I"))
    return fields


def ranges(codepoints: list[int]) -> list[list[int]]:
    result: list[list[int]] = []
    for codepoint in sorted(set(codepoints)):
        if result and codepoint == result[-1][1] + 1:
            result[-1][1] = codepoint
        else:
            result.append([codepoint, codepoint])
    return result


class StringTable:
    """One STRG ownership/UTF-8 contract shared by the index, LANG and FONT."""

    def __init__(self, reader: Reader, region: Region):
        self.reader = reader
        self.pointers, self.bounds = reader.pointer_list(
            region.start, region, MAX_STRINGS, 5)

    def string(self, pointer: int) -> str:
        if pointer - 4 not in self.bounds:
            raise InspectorError("String ptr nie wskazuje treści wpisu STRG")
        bound = self.bounds[pointer - 4]
        size = self.reader.scalar("i", pointer - 4, bound)
        if not 0 <= size <= MAX_STRING_BYTES:
            raise InspectorError("Długość string poza limitem")
        bound.check(pointer, size + 1)
        data = self.reader.data
        if data[pointer + size] != 0:
            raise InspectorError("Brak NUL po treści string")
        raw = data[pointer:pointer + size]
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise InspectorError("Niepoprawny UTF-8 string") from exc
        if text.encode("utf-8") != raw or "\x00" in text:
            raise InspectorError("Niedozwolony string / niezgodny round-trip UTF-8")
        return text

    def report(self) -> dict:
        # Validate every entry, including after the hit budget is exhausted.
        # Do not retain decoded corpus strings or publish a full STRG dump.
        hits = []
        matched = total_bytes = 0
        term_counts: dict[str, int] = dict.fromkeys(STRG_TERMS, 0)
        for index, object_pointer in enumerate(self.pointers):
            pointer = object_pointer + 4
            text = self.string(pointer)
            size = self.reader.scalar("i", object_pointer, self.bounds[object_pointer])
            total_bytes += size
            folded = text.casefold()
            terms = [term for term in STRG_TERMS if term in folded]
            for term in terms:
                term_counts[term] += 1
            if terms:
                matched += 1
                if len(hits) < MAX_STRG_HITS:
                    hits.append({"index": index, "object_pointer": object_pointer,
                                 "content_pointer": pointer, "byte_length": size,
                                 "terms": terms, "preview": text[:MAX_HIT_CHARS],
                                 "preview_truncated": len(text) > MAX_HIT_CHARS})
        return {"strg_summary": {"count": len(self.pointers),
                                 "total_string_bytes": total_bytes,
                                 "validated_count": len(self.pointers)},
                "strg_hits": {"terms": list(STRG_TERMS), "term_counts": term_counts,
                              "matched_count": matched, "reported_count": len(hits),
                              "omitted_count": matched - len(hits),
                              "limit": MAX_STRG_HITS, "preview_char_limit": MAX_HIT_CHARS,
                              "items": hits}}


def parse_lang(r: Reader, region: Region | None, strings: StringTable,
               chunks: list[dict]) -> dict | None:
    """UMT LANG: inline counts/tables, absolute content pointers into STRG."""
    if region is None:
        return None
    region.check(region.start, 12)
    unknown1, languages, entries = r.unpack("3I", region.start, region)
    if languages > MAX_LANGUAGES or entries > MAX_LANG_ENTRIES:
        raise InspectorError("LANG: liczba języków lub entry IDs przekracza limit")
    if languages * entries > MAX_LANG_TRANSLATIONS:
        raise InspectorError("LANG: łączna liczba tłumaczeń przekracza limit")
    model_size = 12 + entries * 4 + languages * (8 + entries * 4)
    region.check(region.start, model_size)
    model_end = region.start + model_size
    padding_size = region.end - model_end
    if padding_size:
        # GMS2 includes zero-padding in the preceding chunk's size. Use absolute
        # offsets and the completed FORM inventory, never scan/guess a header.
        if (padding_size != (-model_end) % 16 or region.end % 16 != 0
                or not any(chunk["header_offset"] == region.end for chunk in chunks)):
            raise InspectorError("LANG: nieznany ogon; wymagany dokładny padding do 16 "
                                 "i zarejestrowany następny chunk na payload_end")
        if any(r.data[model_end:region.end]):
            raise InspectorError("LANG: niezerowy padding")
    text_budget = 0

    def string(pointer: int) -> str:
        nonlocal text_budget
        text = strings.string(pointer)
        # Charge every serialized occurrence, even repeated pointers. ASCII JSON
        # escaping can be much larger than UTF-8 (including non-BMP Unicode).
        text_budget += len(json.dumps(text, ensure_ascii=True))
        if text_budget > MAX_LANG_TEXT_JSON:
            raise InspectorError("LANG: przekroczony limit tekstu JSON; brak obcinania raportu")
        return text

    cursor = region.start + 12
    ids = list(r.unpack(f"{entries}I", cursor, region))
    entry_ids = [string(pointer) for pointer in ids]
    cursor += entries * 4
    records = []
    for index in range(languages):
        name, region_ptr = r.unpack("2I", cursor, region)
        translated = list(r.unpack(f"{entries}I", cursor + 8, region))
        records.append({"index": index, "name_pointer": name, "name": string(name),
                        "region_pointer": region_ptr, "region": string(region_ptr),
                        "translation_pointers": translated,
                        "translations": [string(pointer) for pointer in translated]})
        cursor += 8 + entries * 4
    return {"offset": region.start, "unknown1": unknown1,
            "model_size": model_size, "padding_size": padding_size,
            "padding_alignment": 16,
            "language_count": languages, "entry_count": entries,
            "entry_id_pointers": ids, "entry_ids": entry_ids, "languages": records}


def partial_report(actual: str, data: bytes, chunks: list[dict], *, text_report: dict,
                   bytecode: int,
                   declared: tuple[int, int, int, int], format_version: str | None,
                   version_evidence: str | None, lts: bool | None,
                   lts_evidence: str | None, blocker: str) -> dict:
    """Publish container/GEN8 and independent STRG/LANG facts, never guess FONT."""
    return {
        **text_report,
        "schema": "crypt-custodian-font-inspection/v1",
        "status": "partial",
        "source": {"sha256": actual, "size": len(data)},
        "chunks": chunks,
        "gen8": {"bytecode": bytecode, "major": declared[0], "minor": declared[1],
                 "release": declared[2], "build": declared[3],
                 "requested_layout_version": (
                     list(version_tuple(format_version)) if format_version else None),
                 "version_evidence": version_evidence, "lts": lts,
                 "lts_evidence": lts_evidence},
        "font_parse_performed": False,
        "blocker": blocker,
        "warnings": [
            STRING_WARNING,
            "FONT, TPAG i glify nie zostały sparsowane bez potwierdzonego layoutu.",
            "Raport częściowy nie deklaruje pokrycia polskich glifów.",
            "Nie wykrywano wersji ani LTS heurystycznie.",
        ],
    }


def inspect_bytes(data: bytes, expected_sha256: str, *,
                  format_version: str | None = None,
                  version_evidence: str | None = None,
                  lts: bool | None = None,
                  lts_evidence: str | None = None) -> dict:
    """Parse one immutable, hash-verified snapshot, without filesystem access."""
    expected = check_hash(expected_sha256)
    if len(data) > MAX_SOURCE:
        raise InspectorError("Przekroczony limit wejścia")
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise InspectorError("Niezgodny SHA-256 źródła")
    if bool(format_version) != bool(version_evidence and version_evidence.strip()):
        raise InspectorError("--format-version wymaga --version-evidence i odwrotnie")
    if (lts is not None) != bool(lts_evidence and lts_evidence.strip()):
        raise InspectorError("--lts wymaga --lts-evidence i odwrotnie")
    r = Reader(data)
    magic, length = r.unpack("4sI", 0, r.file)
    if magic != b"FORM" or length != len(data) - 8:
        raise InspectorError("Niepoprawny FORM lub rozmiar (także bajty za FORM)")
    chunks = []
    regions: dict[str, Region] = {}
    cursor = 8
    while cursor < len(data):
        if len(chunks) >= MAX_CHUNKS:
            raise InspectorError("Przekroczony limit chunków")
        tag, size = r.unpack("4sI", cursor, r.file)
        if not re.fullmatch(rb"[A-Z0-9]{4}", tag):
            raise InspectorError("Niepoprawny identyfikator chunku")
        name = tag.decode("ascii")
        if name in regions:
            raise InspectorError(f"Powtórzony chunk {name}")
        payload = cursor + 8
        r.file.check(payload, size)
        regions[name] = Region(payload, payload + size, name)
        chunks.append({"name": name, "header_offset": cursor,
                       "payload_offset": payload, "size": size})
        cursor = payload + size
    if "GEN8" not in regions:
        raise InspectorError("Brak wymaganego chunku GEN8")
    gen = regions["GEN8"]
    gen.check(gen.start, 0x3C)
    bytecode = r.scalar("B", gen.start + 1, gen)
    declared = r.unpack("4I", gen.start + 0x2C, gen)
    if "STRG" not in regions:
        raise InspectorError("Brak wymaganego chunku STRG")
    strings = StringTable(r, regions["STRG"])
    text_report = strings.report()
    text_report["lang"] = parse_lang(r, regions.get("LANG"), strings, chunks)
    effective = version_tuple(format_version) if format_version else declared
    if effective == (2, 0, 0, 0) and not format_version:
        return partial_report(
            actual, data, chunks, text_report=text_report, bytecode=bytecode, declared=declared,
            format_version=format_version, version_evidence=version_evidence, lts=lts,
            lts_evidence=lts_evidence,
            blocker=("GEN8 2.0.0.0 nie rozstrzyga współczesnego layoutu; wymagana "
                     "potwierdzona --format-version z --version-evidence"),
        )
    try:
        fields = version_fields(effective, bytecode, lts)
    except InspectorError as exc:
        return partial_report(
            actual, data, chunks, text_report=text_report, bytecode=bytecode, declared=declared,
            format_version=format_version, version_evidence=version_evidence, lts=lts,
            lts_evidence=lts_evidence, blocker=str(exc),
        )
    for name in ("FONT", "TPAG"):
        if name not in regions:
            raise InspectorError(f"Brak wymaganego chunku {name}")
    warnings = [
        STRING_WARNING,
        "Inwentaryzacja statyczna nie potwierdza użycia fontu przez runtime.",
        "Obecność kodu glifu nie dowodzi poprawnego obrazu ani szerokości tekstu.",
        "TXTR nie jest dekodowany; texture_page_id nie jest weryfikowany w TXTR.",
        "Nie wykrywano wersji heurystycznie; GEN8 nie zawsze podaje wersję layoutu.",
    ]
    if format_version:
        warnings.append("Wersja layoutu jest deklaracją operatora, nie automatyczną detekcją.")
    if lts is not None:
        warnings.append("Klasyfikacja LTS jest deklaracją operatora; dowód zapisano w GEN8.")

    tpag = regions["TPAG"]
    tpointers, tbounds = r.pointer_list(tpag.start, tpag, MAX_TPAG, 22)
    texture_items = []
    texture_index = {}
    names = ("source_x", "source_y", "source_width", "source_height", "target_x",
             "target_y", "target_width", "target_height", "bounding_width",
             "bounding_height", "texture_page_id")
    for index, pointer in enumerate(tpointers):
        item: dict = dict(zip(names, r.unpack("10Hh", pointer, tbounds[pointer])))
        if item["texture_page_id"] < -1:
            raise InspectorError("TPAG: niedozwolone ujemne texture_page_id")
        item.update(index=index, offset=pointer)
        texture_index[pointer] = index
        texture_items.append(item)

    font_region = regions["FONT"]
    prefix_size = 0x28 + 4 * len(fields)
    fpointers, fbounds = r.pointer_list(font_region.start, font_region, MAX_FONTS,
                                      prefix_size + 4)
    fonts = []
    total_glyphs = total_kerns = 0
    for index, pointer in enumerate(fpointers):
        bound = fbounds[pointer]
        name_ptr, display_ptr, em_raw, bold, italic = r.unpack("5I", pointer, bound)
        if bold not in (0, 1) or italic not in (0, 1):
            raise InspectorError("FONT: Bold/Italic nie jest bool32 0/1")
        start, charset, aa, end, texture, sx, sy = r.unpack("HBBIIff", pointer + 0x14, bound)
        em_float = bool(em_raw & 0x80000000)
        em_size = -r.scalar("f", pointer + 8, bound) if em_float else em_raw
        if not all(math.isfinite(value) for value in (em_size, sx, sy)):
            raise InspectorError("FONT: niefinitywne EmSize/Scale")
        if start > end or end > 0x10FFFF:
            raise InspectorError("FONT: niepoprawny deklarowany zakres znaków")
        if texture and texture not in texture_index:
            raise InspectorError("FONT.Texture nie wskazuje początku rekordu TPAG")
        version_values = {}
        for i, (field, fmt) in enumerate(fields):
            version_values[field] = r.scalar(fmt, pointer + 0x28 + i * 4, bound)
        list_offset = pointer + prefix_size
        gpointers, gbounds = r.pointer_list(list_offset, bound, MAX_GLYPHS,
                                           18 if effective >= (2024, 11, 0, 0) else 16)
        total_glyphs += len(gpointers)
        if total_glyphs > MAX_TOTAL_GLYPHS:
            raise InspectorError("Przekroczony łączny limit glifów")
        glyphs = []
        seen = set()
        for gp in gpointers:
            gb = gbounds[gp]
            cp, x, y, width, height, shift, offset = r.unpack("5H2h", gp, gb)
            if cp in seen or 0xD800 <= cp <= 0xDFFF:
                raise InspectorError("Powtórzony kod glifu lub surogat UTF-16")
            seen.add(cp)
            kc_offset = gp + 0x0E
            zero = None
            if effective >= (2024, 11, 0, 0):
                zero = r.scalar("h", kc_offset, gb)
                if zero != 0:
                    raise InspectorError("Glyph.UnknownAlwaysZero nie jest zerem")
                kc_offset += 2
            count = r.scalar("H", kc_offset, gb)
            total_kerns += count
            if total_kerns > MAX_TOTAL_KERNING:
                raise InspectorError("Przekroczony łączny limit kerningu")
            gb.check(kc_offset + 2, count * 4)
            kerning = [list(r.unpack("hh", kc_offset + 2 + i * 4, gb))
                       for i in range(count)]
            glyphs.append({"offset": gp, "codepoint": cp, "unicode": f"U+{cp:04X}",
                           "character": chr(cp), "source_x": x, "source_y": y,
                           "source_width": width, "source_height": height,
                           "shift": shift, "bearing_offset": offset,
                           "unknown_always_zero": zero, "kerning_count": count,
                           "kerning": kerning})
        codepoints = sorted(seen)
        present = [char for char in POLISH if ord(char) in seen]
        missing = [char for char in POLISH if ord(char) not in seen]
        font_warnings = []
        if missing:
            font_warnings.append("Brak części polskich liter w liście glifów.")
        if not texture:
            font_warnings.append("Texture ptr = NULL; brak powiązania TPAG.")
        if any(cp < start or cp > end for cp in seen):
            font_warnings.append("Glify wychodzą poza deklarowany RangeStart/RangeEnd.")
        if not glyphs:
            font_warnings.append("Pusta lista glifów.")
        fonts.append({"index": index, "offset": pointer, "name": strings.string(name_ptr),
                      "display_name": strings.string(display_ptr), "em_size_raw_u32": em_raw,
                      "em_size": em_size, "em_size_is_float": em_float,
                      "bold": bool(bold), "italic": bool(italic),
                      "declared_range": [start, end], "charset": charset, "aa": aa,
                      "scale_x": sx, "scale_y": sy, "version_fields": version_values,
                      "texture_pointer": texture,
                      "tpag_index": texture_index.get(texture),
                      "glyph_list_offset": list_offset, "glyph_count": len(glyphs),
                      "codepoints": codepoints, "glyph_ranges": ranges(codepoints),
                      "glyphs": glyphs, "polish": {"required": list(POLISH),
                      "present": present, "missing": missing, "complete": not missing},
                      "warnings": font_warnings})
    if not fonts:
        warnings.append("Brak fontów; kompletność polskich znaków nie jest potwierdzona.")
    return {**text_report, "schema": "crypt-custodian-font-inspection/v1", "status": "ok",
             "source": {"sha256": actual, "size": len(data)}, "chunks": chunks,
            "gen8": {"bytecode": bytecode, "major": declared[0], "minor": declared[1],
                     "release": declared[2], "build": declared[3],
                     "effective_layout_version": list(effective),
                     "version_source": "operator" if format_version else "GEN8",
                     "version_evidence": version_evidence, "lts": lts,
                     "lts_evidence": lts_evidence},
             "font_parse_performed": True, "font_count": len(fonts), "fonts": fonts, "tpag": texture_items,
            "total_glyph_count": total_glyphs, "total_kerning_count": total_kerns,
            "polish_complete_in_every_font": bool(fonts) and all(
                font["polish"]["complete"] for font in fonts), "warnings": warnings}


def reject_links(path: Path) -> None:
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise InspectorError(f"Niedozwolone dowiązanie/reparse point: {part}")


def read_source(source: Path) -> bytes:
    if not source.is_absolute() or ".." in source.parts or source.name.lower() != "data.win":
        raise InspectorError("--source wymaga bezwzględnej ścieżki do data.win bez '..'")
    reject_links(source)
    if not stat.S_ISREG(source.stat().st_mode):
        raise InspectorError("Źródło nie jest zwykłym plikiem")
    with source.open("rb") as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise InspectorError("Otwarte źródło nie jest zwykłym plikiem")
        if before.st_size > MAX_SOURCE:
            raise InspectorError("Przekroczony limit wejścia")
        data = handle.read(before.st_size + 1)
        after = os.fstat(handle.fileno())
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                             info.st_ctime_ns)
    if identity(before) != identity(after) or len(data) != before.st_size:
        raise InspectorError("Źródło zmieniło się podczas odczytu")
    return data


def new_output_path(value: str, source: Path) -> Path:
    path = Path(value)
    if ".." in path.parts:
        raise InspectorError("'..' niedozwolone w --output")
    path = path if path.is_absolute() else ROOT / path
    reject_links(WORK)
    reject_links(path)
    resolved = path.resolve()
    if not resolved.is_relative_to(WORK.resolve()) or resolved == WORK.resolve():
        raise InspectorError("--output musi wskazywać nowy JSON wewnątrz work/")
    unsafe_parts = any(
        PureWindowsPath(part).is_reserved() or ":" in part or part.endswith((" ", "."))
        for part in path.parts[1:]
    )
    if path.suffix.lower() != ".json" or unsafe_parts:
        raise InspectorError("Wymagany zwykły plik .json (bez ADS)")
    if path.exists() or resolved == source.resolve():
        raise InspectorError("Nie wolno nadpisywać istniejącego pliku")
    if not path.parent.is_dir():
        raise InspectorError("Katalog docelowy musi już istnieć; brak automatycznego mkdir")
    return path


def write_report(value: str, source: Path, report: dict) -> None:
    payload = json.dumps(report, ensure_ascii=True, allow_nan=False, indent=2) + "\n"
    if len(payload) > MAX_REPORT:
        raise InspectorError("Przekroczony limit raportu JSON")
    path = new_output_path(value, source)
    # Exclusive creation, never replace/rename/unlink. Only this path can be written.
    with path.open("x", encoding="ascii", newline="\n") as handle:
        handle.write(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="odczytowa inwentaryzacja FONT/STRG/LANG")
    inspect.add_argument("--source", required=True)
    inspect.add_argument("--expected-sha256", required=True)
    inspect.add_argument("--output")
    inspect.add_argument("--format-version")
    inspect.add_argument("--version-evidence")
    inspect.add_argument("--lts", choices=("yes", "no"))
    inspect.add_argument("--lts-evidence")
    args = parser.parse_args(argv)
    try:
        check_hash(args.expected_sha256)
        source = Path(args.source)
        if args.output:
            new_output_path(args.output, source)
        report = inspect_bytes(read_source(source), args.expected_sha256,
                               format_version=args.format_version,
                               version_evidence=args.version_evidence,
                               lts=None if args.lts is None else args.lts == "yes",
                               lts_evidence=args.lts_evidence)
        report["source"]["path"] = str(source)
        if args.output:
            write_report(args.output, source, report)
        else:
            text = json.dumps(report, ensure_ascii=True, allow_nan=False, indent=2)
            if len(text) > MAX_REPORT:
                raise InspectorError("Przekroczony limit raportu JSON")
            print(text)
        return 0
    except (InspectorError, OSError) as exc:
        print(f"Odmowa inspekcji: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
