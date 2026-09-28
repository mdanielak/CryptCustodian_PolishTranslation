"""Read-only, bounded PE32+ string/reference inspection; Python 3.10+ stdlib.

See YYC_STRING_INSPECTOR.md for the intentionally narrow decoding contract.
No target executable is ever loaded or executed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
from dataclasses import asdict, dataclass


WORKSHOP = Path(__file__).absolute().parent
TERMS = (
    "translations.ini", "translations2.ini", "translations - Copy.ini",
    "ini_open", "ini_read_string", "english", "language", "polish",
)
MAX_SOURCE_BYTES = 512 * 1024 * 1024
MAX_SECTIONS = 96
MAX_LITERALS = 8192
MAX_XREFS = 32768
MAX_REPORT_BYTES = 32 * 1024 * 1024
REPARSE_POINT = 0x400
U32_END = 1 << 32
U64_END = 1 << 64
EXECUTE = 0x20000000
READ = 0x40000000
WRITE = 0x80000000
# Canonical REX.W, optionally REX.R; redundant REX.X/B are out of scope.
# Lookahead retains overlapping byte candidates; no instruction-boundary claim.
RIP_PATTERN = re.compile(
    rb"(?=([\x48\x4c][\x8d\x8b][\x05\x0d\x15\x1d\x25\x2d\x35\x3d].{4}))",
    re.DOTALL,
)
PREFIX_BYTES = frozenset((0xF0, 0xF2, 0xF3, 0x2E, 0x36, 0x3E,
                          0x26, 0x64, 0x65, 0x66, 0x67, *range(0x40, 0x50)))
REGISTERS = ("rax", "rcx", "rdx", "rbx", "rsp", "rbp", "rsi", "rdi",
             "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15")
WARNINGS = [
    "Static bytes only: neither literals nor reference candidates establish "
    "callsites, instruction boundaries, reachability, runtime use or an active INI.",
    "No xref does not exclude dynamic string construction, pointer tables, "
    "indirect references or instruction forms outside this narrow decoder.",
    "MOV reads memory at the literal address; it does not load that address "
    "as a pointer. LEA computes the address. Neither is a call.",
    "Matching is case-sensitive, exact bytes followed by NUL. An unproven "
    "start boundary can be a suffix of a longer string; no boundary is invented.",
    "VA uses the preferred ImageBase, not an observed ASLR/runtime address. "
    "No relocations, imports, pointer tables or general disassembly are processed.",
    "The decoder accepts only canonical 7-byte REX.W 48/4c LEA/MOV forms. "
    "An immediately preceding prefix-like byte suppresses the candidate; "
    "this conservative rule can miss real instructions.",
    "Path checks are not a sandbox against hostile concurrent directory "
    "replacement. Use a trusted local workshop with no concurrent writers.",
]


class InspectorError(Exception):
    """Input, safety or resource-limit failure; no successful report."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise InspectorError(message)


def _plain_path(path: Path) -> None:
    require(path.is_absolute(), "Path must be absolute")
    require(".." not in path.parts, "Parent traversal is forbidden")
    # Reject UNC/device namespaces as well as Windows ADS/reserved names,
    # including when running the synthetic tests on POSIX.
    require(not str(path).startswith(("\\\\", "//")), "UNC/device paths forbidden")
    for part in path.parts[1:]:
        require(not any(c in part for c in ':<>"|?*\\'), "Unsafe path component")
        require(not any(ord(c) < 32 for c in part), "Control character in path")
        require(not part.endswith((" ", ".")), "Trailing dot/space forbidden")
        stem = part.split(".")[0].upper()
        require(stem not in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
                and not re.fullmatch(r"(?:COM|LPT)[1-9¹²³]", stem),
                "Reserved device name")


def _not_link(info: os.stat_result) -> None:
    require(not stat.S_ISLNK(info.st_mode)
            and not (getattr(info, "st_file_attributes", 0) & REPARSE_POINT),
            "Symlink/reparse point forbidden")


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _stamp(info: os.stat_result) -> tuple[int, ...]:
    return (*_identity(info), info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def _same_identity_and_size(left: os.stat_result, right: os.stat_result) -> bool:
    """Compare only metadata stable across a path stat and an open handle.

    Some Windows filesystems expose different timestamp views through lstat and
    fstat.  A zero inode is likewise not useful as an identity assertion.
    """
    if left.st_dev != right.st_dev or left.st_size != right.st_size:
        return False
    return not (left.st_ino and right.st_ino) or left.st_ino == right.st_ino


def _check_chain(path: Path, *, directory: bool = False) -> tuple:
    """lstat every existing component, without resolve() following links."""
    _plain_path(path)
    chain = []
    for part in (*reversed(path.parents), path):
        info = part.lstat()
        _not_link(info)
        if part != path or directory:
            require(stat.S_ISDIR(info.st_mode), "Expected ordinary directory")
        else:
            require(stat.S_ISREG(info.st_mode), "Expected ordinary file")
        chain.append((_identity(info), info.st_mode))
    return tuple(chain)


def _read_chunks(stream, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        chunk = stream.read(min(1024 * 1024, remaining))
        require(bool(chunk), "Source shrank during read")
        chunks.append(chunk)
        remaining -= len(chunk)
    require(not stream.read(1), "Source grew during read")
    return b"".join(chunks)


def read_snapshot(source: Path, expected_sha256: str) -> tuple[bytes, str]:
    """Open only rb; parse/hash this immutable snapshot, never reopen to parse."""
    require(bool(re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256)),
            "Expected SHA256 must contain exactly 64 hex digits")
    _plain_path(source)
    require(source.name == "CryptCustodian.exe", "Source must be named CryptCustodian.exe")
    chain = _check_chain(source)
    before = source.lstat()
    require(0 < before.st_size <= MAX_SOURCE_BYTES, "Source size limit exceeded or empty")

    def opener(name, flags):
        return os.open(name, flags | getattr(os, "O_NOFOLLOW", 0)
                       | getattr(os, "O_NONBLOCK", 0))

    with open(source, "rb", opener=opener) as stream:
        opened = os.fstat(stream.fileno())
        _not_link(opened)
        require(stat.S_ISREG(opened.st_mode), "Opened source is not a regular file")
        require(_same_identity_and_size(before, opened), "Source changed before read")
        require(chain == _check_chain(source), "Source path changed before read")
        data = _read_chunks(stream, opened.st_size)
        require(_stamp(opened) == _stamp(os.fstat(stream.fileno())),
                "Source changed during read")
        require(chain == _check_chain(source), "Source path changed during read")
        require(_same_identity_and_size(opened, source.lstat()),
                "Source replaced during read")
    digest = hashlib.sha256(data).hexdigest()
    require(digest == expected_sha256.lower(), "SHA256 mismatch")
    return data, digest


@dataclass(frozen=True)
class Section:
    index: int
    name: str
    name_hex: str
    virtual_size: int
    rva: int
    raw_size: int
    raw_offset: int
    characteristics: int

    @property
    def span(self) -> int:
        return max(self.virtual_size, self.raw_size)

    def classification(self) -> dict:
        c = self.characteristics
        return {
            "executable": bool(c & EXECUTE), "readable": bool(c & READ),
            "writable": bool(c & WRITE), "code": bool(c & 0x20),
            "initialized_data": bool(c & 0x40),
            "uninitialized_data": bool(c & 0x80),
        }


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def _disjoint(ranges: list[tuple[int, int]], label: str) -> None:
    ordered = sorted(ranges)
    for previous, current in zip(ordered, ordered[1:]):
        require(previous[1] <= current[0], f"Overlapping {label}")


class PEImage:
    """Strict structural subset of PE32+ AMD64, not a Windows loader."""

    def __init__(self, data: bytes):
        self.data = data
        require(0 < len(data) <= MAX_SOURCE_BYTES, "Source size limit exceeded or empty")
        self._range(0, 64)
        require(data[:2] == b"MZ", "Missing DOS MZ signature")
        pe = self.u32(0x3C)
        require(pe >= 64 and pe % 4 == 0, "Invalid e_lfanew")
        self._range(pe, 24)
        require(data[pe:pe + 4] == b"PE\0\0", "Missing PE signature")
        require(self.u16(pe + 4) == 0x8664, "Expected AMD64 machine")
        count = self.u16(pe + 6)
        require(1 <= count <= MAX_SECTIONS, "Invalid section count")
        optional_size = self.u16(pe + 20)
        flags = self.u16(pe + 22)
        require(bool(flags & 2) and not flags & 0x100,
                "Expected executable, non-32-bit COFF characteristics")
        optional = pe + 24
        require(optional_size >= 112, "Truncated PE32+ optional header")
        self._range(optional, optional_size)
        require(self.u16(optional) == 0x20B, "Expected PE32+ optional magic")
        self.image_base = self.u64(optional + 24)
        self.section_alignment = self.u32(optional + 32)
        self.file_alignment = self.u32(optional + 36)
        self.size_of_image = self.u32(optional + 56)
        self.size_of_headers = self.u32(optional + 60)
        sa, fa = self.section_alignment, self.file_alignment
        require(sa > 0 and sa & (sa - 1) == 0, "Invalid SectionAlignment")
        require(fa > 0 and fa & (fa - 1) == 0, "Invalid FileAlignment")
        require(sa >= fa and ((sa < 4096 and sa == fa)
                             or (sa >= 4096 and 512 <= fa <= 65536)),
                "Unsupported PE alignment relationship")
        require(self.image_base % 65536 == 0, "ImageBase is not 64K aligned")
        require(0 < self.size_of_image < U32_END and self.size_of_image % sa == 0,
                "Invalid SizeOfImage")
        require(self.image_base + self.size_of_image <= U64_END, "VA overflow")
        directories = self.u32(optional + 108)
        require(directories <= 16 and 112 + 8 * directories <= optional_size,
                "Invalid data-directory count/optional-header size")
        table = optional + optional_size
        table_end = table + count * 40
        self._range(table, count * 40)
        require(table_end <= self.size_of_headers <= len(data)
                and self.size_of_headers % fa == 0, "Invalid SizeOfHeaders")
        headers_end = _align(self.size_of_headers, sa)
        require(headers_end <= self.size_of_image, "Headers exceed image")
        raw_ranges = [(0, self.size_of_headers)]
        virtual_ranges = [(0, headers_end)]
        self.sections = []
        for index in range(count):
            offset = table + index * 40
            name = data[offset:offset + 8]
            vs, rva, rs, raw = struct.unpack_from("<IIII", data, offset + 8)
            section = Section(index, name.split(b"\0", 1)[0].decode("ascii", "backslashreplace"),
                              name.hex(), vs, rva, rs, raw, self.u32(offset + 36))
            require(rva % sa == 0 and rva <= self.size_of_image,
                    "Misaligned or out-of-image section RVA")
            if section.span:
                end = rva + _align(section.span, sa)
                require(end <= self.size_of_image and end <= U32_END,
                        "Section exceeds virtual image")
                virtual_ranges.append((rva, end))
            if rs:
                require(raw % fa == 0 and rs % fa == 0, "Misaligned raw section")
                self._range(raw, rs)
                require(sa >= 4096 or raw == rva, "Low-alignment raw/RVA mismatch")
                raw_ranges.append((raw, raw + rs))
            else:
                require(raw == 0, "Empty raw section must use zero pointer")
            self.sections.append(section)
        _disjoint(raw_ranges, "raw sections/headers")
        _disjoint(virtual_ranges, "virtual sections/headers")
        entry = self.u32(optional + 16)
        if entry:
            require(any(s.rva <= entry < s.rva + s.span and s.characteristics & EXECUTE
                        for s in self.sections), "Entry point outside executable section")
        self.entry_point_rva = entry
        # Directory payloads are not interpreted. Security directory uses file
        # offsets; the rest use RVAs and may legitimately include zero-fill.
        for index in range(directories):
            address, size = struct.unpack_from("<II", data, optional + 112 + index * 8)
            require(bool(address) == bool(size), "Incomplete data-directory range")
            if not size:
                continue
            if index == 4:
                require(address % 8 == 0, "Misaligned certificate directory")
                self._range(address, size)
                require(all(address + size <= a or address >= b for a, b in raw_ranges),
                        "Certificate directory overlaps mapped data")
            else:
                require(address + size <= U32_END and (
                    address + size <= self.size_of_headers or any(
                        s.rva <= address and address + size <= s.rva + s.span
                        for s in self.sections)), "Data directory outside mapped range")

    def _range(self, offset: int, size: int) -> None:
        require(offset >= 0 and size >= 0 and offset + size <= len(self.data),
                "File range out of bounds")

    def u16(self, offset: int) -> int:
        self._range(offset, 2)
        return struct.unpack_from("<H", self.data, offset)[0]

    def u32(self, offset: int) -> int:
        self._range(offset, 4)
        return struct.unpack_from("<I", self.data, offset)[0]

    def u64(self, offset: int) -> int:
        self._range(offset, 8)
        return struct.unpack_from("<Q", self.data, offset)[0]

    def offset_to_rva(self, offset: int, size: int = 1) -> int | None:
        require(size > 0, "Mapping size must be positive")
        self._range(offset, size)
        if offset + size <= self.size_of_headers:
            return offset
        for s in self.sections:
            if s.raw_size and s.raw_offset <= offset and offset + size <= s.raw_offset + s.raw_size:
                return s.rva + offset - s.raw_offset
        return None

    def rva_to_offset(self, rva: int, size: int = 1) -> int | None:
        require(0 <= rva < U32_END and size > 0 and rva + size <= U32_END,
                "Invalid RVA range")
        if rva + size <= self.size_of_headers:
            return rva
        for s in self.sections:
            if s.rva <= rva and rva + size <= s.rva + s.raw_size:
                return s.raw_offset + rva - s.rva
        return None  # zero-fill and alignment tails have no file bytes

    def va_to_offset(self, va: int, size: int = 1) -> int | None:
        if not self.image_base <= va < self.image_base + self.size_of_image:
            return None
        return self.rva_to_offset(va - self.image_base, size)

    def location(self, offset: int, size: int) -> dict:
        rva = self.offset_to_rva(offset, size)
        section = next((s for s in self.sections if s.raw_size
                        and s.raw_offset <= offset < s.raw_offset + s.raw_size), None)
        if rva is not None:
            kind = "section" if section is not None else "headers"
        elif section is not None or offset < self.size_of_headers:
            kind = "cross_boundary"
        else:
            last_raw = max([self.size_of_headers]
                           + [s.raw_offset + s.raw_size for s in self.sections if s.raw_size])
            kind = "overlay" if offset >= last_raw else "unmapped_gap"
        return {
            "file_offset": offset, "rva": rva,
            "va": None if rva is None else self.image_base + rva,
            "region": kind,
            "section_index": None if section is None else section.index,
            "section_name": None if section is None else section.name,
            "section_flags": None if section is None else section.classification(),
            "includes_raw_padding": bool(section and section.virtual_size
                and offset + size > section.raw_offset + section.virtual_size),
        }


def find_literals(image: PEImage) -> list[dict]:
    hits = []
    data = image.data
    for term in TERMS:
        for encoding, codec, nul in (("ascii/utf-8", "utf-8", b"\0"),
                                     ("utf-16le", "utf-16le", b"\0\0")):
            payload = term.encode(codec)
            needle = payload + nul
            position = 0
            while True:
                offset = data.find(needle, position)
                if offset < 0:
                    break
                require(len(hits) < MAX_LITERALS, "Literal limit exceeded; no partial report")
                position = offset + 1  # include odd UTF-16 offsets and overlapping occurrences
                location = image.location(offset, len(needle))
                section_index = location["section_index"]
                region_start = (image.sections[section_index].raw_offset
                                if section_index is not None else 0)
                if offset == region_start:
                    boundary = "region_start"
                elif offset >= len(nul) and data[offset - len(nul):offset] == nul:
                    boundary = "preceding_nul"
                else:
                    boundary = "unproven"
                require(data[offset:offset + len(payload)].decode(codec).encode(codec) == payload,
                        "String encoding round-trip failed")
                hits.append({
                    "term": term, "encoding": encoding, **location,
                    "byte_length": len(payload), "terminator_bytes": len(nul),
                    "start_boundary": boundary, "xref_ids": [],
                })
    hits.sort(key=lambda hit: (hit["file_offset"], hit["encoding"], hit["term"]))
    for index, hit in enumerate(hits):
        hit["id"] = index
    return hits


def find_xrefs(image: PEImage, literals: list[dict]) -> list[dict]:
    targets: dict[int, list[dict]] = {}
    for literal in literals:
        if literal["va"] is not None:
            targets.setdefault(literal["va"], []).append(literal)
    xrefs = []
    for section in image.sections:
        if not section.characteristics & EXECUTE or not section.raw_size:
            continue
        start, end = section.raw_offset, section.raw_offset + section.raw_size
        for match in RIP_PATTERN.finditer(image.data, start, end):
            offset = match.start()
            if offset > start and image.data[offset - 1] in PREFIX_BYTES:
                continue  # possible additional prefix, intentionally not decoded
            instruction = match.group(1)
            rex, opcode, modrm = instruction[:3]
            require(rex in (0x48, 0x4C) and opcode in (0x8D, 0x8B)
                    and modrm & 0xC7 == 0x05, "Internal decoder invariant")
            rva = section.rva + offset - start
            displacement = struct.unpack_from("<i", instruction, 3)[0]
            target_va = image.image_base + rva + 7 + displacement
            if not 0 <= target_va < U64_END or target_va not in targets:
                continue
            require(len(xrefs) < MAX_XREFS, "Xref limit exceeded; no partial report")
            register = ((modrm >> 3) & 7) + (8 if rex & 4 else 0)
            record = {
                "id": len(xrefs), "kind": "rip_relative_byte_candidate",
                "mnemonic": "LEA" if opcode == 0x8D else "MOV",
                "rex": f"{rex:02x}", "opcode": f"{opcode:02x}",
                "modrm": f"{modrm:02x}", "register": REGISTERS[register],
                "instruction_hex": instruction.hex(), "instruction_size": 7,
                "file_offset": offset, "rva": rva, "va": image.image_base + rva,
                "section_index": section.index, "section_name": section.name,
                "disp32": displacement, "target_va": target_va,
                "target_rva": target_va - image.image_base,
                "target_file_offset": image.va_to_offset(target_va),
                "literal_ids": [hit["id"] for hit in targets[target_va]],
            }
            xrefs.append(record)
            for hit in targets[target_va]:
                hit["xref_ids"].append(record["id"])
    return xrefs


def inspect_snapshot(data: bytes, source: str, digest: str) -> dict:
    """Pure analysis entry point; CLI obtains data/digest via read_snapshot."""
    image = PEImage(data)
    literals = find_literals(image)
    xrefs = find_xrefs(image, literals)
    return {
        "schema": "crypt-custodian-yyc-string-inspection/v1",
        "status": "ok", "analysis": "static_byte_candidates_only",
        "source": {"path": source, "size": len(data), "sha256": digest},
        "pe": {
            "machine": "AMD64", "optional_magic": "PE32+",
            "image_base": image.image_base, "size_of_image": image.size_of_image,
            "size_of_headers": image.size_of_headers,
            "section_alignment": image.section_alignment,
            "file_alignment": image.file_alignment,
            "entry_point_rva": image.entry_point_rva,
            "sections": [{**asdict(s), "flags": s.classification()} for s in image.sections],
        },
        "terms": list(TERMS), "literal_count": len(literals), "xref_count": len(xrefs),
        "term_counts": {term: sum(hit["term"] == term for hit in literals) for term in TERMS},
        "literals": literals, "xrefs": xrefs,
        "limits": {"source_bytes": MAX_SOURCE_BYTES, "sections": MAX_SECTIONS,
                   "literals": MAX_LITERALS, "xrefs": MAX_XREFS,
                   "report_bytes": MAX_REPORT_BYTES},
        "truncated": False, "warnings": list(WARNINGS),
    }


def encode_report(report: dict) -> bytes:
    # Incremental encoding also bounds allocations when a caller supplies a report.
    result = bytearray()
    encoder = json.JSONEncoder(ensure_ascii=True, indent=2, allow_nan=False)
    for text in encoder.iterencode(report):
        chunk = text.encode("ascii")
        require(len(result) + len(chunk) + 1 <= MAX_REPORT_BYTES, "Report size limit exceeded")
        result.extend(chunk)
    result.extend(b"\n")
    return bytes(result)


def output_path(value: str) -> Path:
    """Only an immediate, new .json child of this script's existing work/."""
    path = Path(value)
    if not path.is_absolute():
        path = WORKSHOP / path
    _plain_path(path)
    work = WORKSHOP / "work"
    require(path.parent == work and path.suffix == ".json",
            "Output must be a direct .json child of workshop/work")
    _check_chain(work, directory=True)
    require(not os.path.lexists(path), "Output already exists; overwrite forbidden")
    return path


def write_report(path: Path, payload: bytes) -> None:
    # Revalidate immediately before exclusive creation, including API callers.
    require(len(payload) <= MAX_REPORT_BYTES, "Report size limit exceeded")
    path = output_path(str(path))
    chain = _check_chain(path.parent, directory=True)

    def opener(name, flags):
        return os.open(name, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)

    with open(path, "xb", opener=opener) as stream:
        info = os.fstat(stream.fileno())
        _not_link(info)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                "Report is not a new single-link regular file")
        require(chain == _check_chain(path.parent, directory=True), "Output directory changed")
        require(_identity(info) == _identity(path.lstat()), "Output path replaced")
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
        _check_chain(path)
        require(chain == _check_chain(path.parent, directory=True), "Output directory changed")
        require(_identity(info) == _identity(path.lstat()), "Output path replaced")
        require(os.fstat(stream.fileno()).st_size == len(payload), "Incomplete report write")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Absolute path to CryptCustodian.exe")
    parser.add_argument("--expected-sha256", required=True, help="Independently verified SHA256")
    parser.add_argument("--output", required=True, help="New work/<name>.json (relative to script)")
    args = parser.parse_args(argv)
    try:
        destination = output_path(args.output)
        data, digest = read_snapshot(Path(args.source), args.expected_sha256)
        report = inspect_snapshot(data, args.source, digest)
        payload = encode_report(report)
        write_report(destination, payload)
    except (InspectorError, OSError, ValueError, OverflowError) as error:
        # ASCII escapes preserve diagnostics even on legacy Windows consoles.
        print("Inspection refused: " + ascii(str(error)), file=sys.stderr)
        return 2
    print("Created new static-inspection JSON; not a runtime/callsite finding.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
