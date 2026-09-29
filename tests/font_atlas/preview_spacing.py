"""Offline pen-space preview of actual glyphs; Python stdlib only, no rasterizer.

Reads a verified atlas bundle and full donor dump. Writes only to a fresh temp
directory under --output-root (must exist outside the project/game).
Incoming kerning is applied before Offset; advance never clips an overhang.
PNG rows A-F match the labelled HTML. This is not a GameMaker runtime test.
"""
import argparse
import hashlib
import html
import json
from pathlib import Path
import struct
import tempfile
import zlib

TEXTS = ("Obudził/aś się!", "ił/", "lł", "łl", "ł/a")
VARIANTS = ("current", "base Shift", "base Shift + optical", "Offset right only",
            "Offset right + base Shift", "Offset right + base Shift + optical")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            assert key not in result, key
            result[key] = value
        return result
    return json.loads(path.read_bytes().decode("utf-8"), object_pairs_hook=unique)


def crop(raw, width, x, y, w, h):
    return b"".join(raw[((y + row) * width + x) * 4:((y + row) * width + x + w) * 4]
                    for row in range(h))


def load(bundle, dump):
    manifest, report = read(bundle / "manifest.json"), read(bundle / "report.json")
    donors = read(dump / "donors.json")
    assert report["status"] == "candidate"
    assert report["sourceSha256"] == donors["sourceSha256"]
    for item in report["outputs"]:
        raw = (bundle / item["file"]).read_bytes()
        assert len(raw) == item["bytes"] and sha(raw) == item["sha256"]
    fonts = []
    for font in manifest["fonts"]:
        name = font["name"]
        donor = next(f for f in donors["fonts"] if f["name"] == name)
        provenance = next(f for f in report["provenance"] if f["name"] == name)
        glyphs = {}
        for f, directory, original in ((donor, dump, True), (font, bundle, False)):
            atlas = f["atlas"]
            raw = (directory / atlas["file"]).read_bytes()
            assert sha(raw) == atlas["sha256"] and len(raw) == atlas["width"] * atlas["height"] * 4
            for g in f["glyphs"]:
                c = chr(g["Character"]) if original else g["character"]
                x, y, w, h = [g[k] for k in (("SourceX", "SourceY", "SourceWidth", "SourceHeight")
                                            if original else ("x", "y", "width", "height"))]
                pixels = crop(raw, atlas["width"], x, y, w, h)
                expected = g["cropRgbaSha256"] if original else next(
                    a["resultSha256"] for a in provenance["additions"] if a["character"] == c)
                assert sha(pixels) == expected
                assert c not in glyphs, "bundle must contain additions only"
                kern = g.get("kerning", [])
                glyphs[c] = dict(w=w, h=h, pixels=pixels, offset=g["Offset" if original else "offset"],
                                 shift=g["Shift" if original else "shift"],
                                 kern={k.get("Character", k.get("character")): k.get("ShiftModifier", k.get("shiftModifier")) for k in kern})
        fonts.append((name, donor["metrics"]["EmSize"], glyphs))
    return fonts


def layout(text, glyphs):
    pen, previous, placed = 0, None, []
    for c in text:
        g = glyphs[c]
        pen += g["kern"].get(previous, 0)
        placed.append((pen + g["offset"], g))
        pen += g["shift"]
        previous = ord(c)
    return placed, pen


def render(canvas, width, height, text, glyphs, x, y):
    placed, _ = layout(text, glyphs)
    for dx, g in placed:
        for yy in range(g["h"]):
            for xx in range(g["w"]):
                si = (yy * g["w"] + xx) * 4
                a = g["pixels"][si + 3]
                if not a:
                    continue
                px, py = x + dx + xx, y + yy
                assert 0 <= px < width and 0 <= py < height, "preview clipped ink"
                di = (py * width + px) * 4
                for channel in range(3):
                    canvas[di + channel] = (g["pixels"][si + channel] * a + canvas[di + channel] * (255 - a) + 127) // 255


def png(raw, w, h):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    scan = b"".join(b"\0" + raw[y * w * 4:(y + 1) * w * 4] for y in range(h))
    compressed = zlib.compress(scan)
    assert zlib.decompress(compressed) == scan
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", compressed) + chunk(b"IEND", b""))


def main():
    parser = argparse.ArgumentParser(__doc__)
    for arg in ("bundle", "donor-dump", "output-root"):
        parser.add_argument("--" + arg, type=Path, required=True)
    args = parser.parse_args()
    root = args.output_root.resolve(strict=True)
    game = Path(__file__).resolve().parents[3]
    assert not root.is_relative_to(game), "preview output must be outside game/project"
    fonts = load(args.bundle, args.donor_dump)
    output = Path(tempfile.mkdtemp(prefix="cc-spacing-preview-", dir=root))
    records, sections = [], []
    for name, em, glyphs in fonts:
        # One 1/32-em optical unit, round-half-up to integer FONT metrics.
        unit = max(1, int(em / 32 + 0.5))
        row_h = max(g["h"] for g in glyphs.values()) + 12
        variants = []
        widths = [0] * len(TEXTS)
        for i, label in enumerate(VARIANTS):
            changed = dict(glyphs["ł"])
            if i in (1, 4):
                changed["shift"] = glyphs["l"]["shift"]
            if i in (2, 5):
                changed["shift"] = glyphs["l"]["shift"] + unit
            if i >= 3:
                changed["offset"] += unit
            gs = dict(glyphs, **{"ł": changed})
            variants.append(gs)
            for j, text in enumerate(TEXTS):
                placed, pen = layout(text, gs)
                widths[j] = max(widths[j], pen, max(x + g["w"] for x, g in placed))
            records.append(dict(font=name, variant=chr(65 + i), label=label, em=em, unit=unit,
                                offset=changed["offset"], shift=changed["shift"], baseShift=glyphs["l"]["shift"],
                                bitmapSha256=sha(changed["pixels"])))
        margin = max(24, int(em / 2))
        label_w = int(em * 2)
        width, height = label_w + sum(w + 2 * margin for w in widths), row_h * len(variants)
        canvas = bytearray(bytes((25, 25, 29, 255)) * width * height)
        for i, gs in enumerate(variants):
            render(canvas, width, height, chr(65 + i) + ":", gs, margin // 2, i * row_h)
            x = label_w + margin
            for j, text in enumerate(TEXTS):
                render(canvas, width, height, text, gs, x, i * row_h)
                x += widths[j] + 2 * margin
        image = png(canvas, width, height)
        file = output / (name + ".png")
        with file.open("xb") as stream:
            stream.write(image)
        assert file.read_bytes() == image
        sections.append(f'<h2>{name}</h2><img src="{name}.png" alt="A-F: spacing variants" style="image-rendering:pixelated;max-width:none">')
    document = '<!doctype html><meta charset="utf-8"><title>ł spacing</title><style>body{background:#19191d;color:white;font:16px sans-serif}table{border-spacing:16px 4px}</style>'
    document += '<h1>Offline: actual RGBA, incoming kerning, no advance clipping</h1><p>Columns: ' + html.escape(" | ".join(TEXTS)) + '</p>'
    document += '<p>' + '<br>'.join(chr(65 + i) + ': ' + label for i, label in enumerate(VARIANTS)) + '</p>'
    document += '<p>Optical unit = max(1, round-half-up(em/32)). All bitmaps identical. Not a runtime validation.</p>'
    document += ''.join(sections)
    for filename, content in (("index.html", document), ("metrics.json", json.dumps(records, ensure_ascii=False, indent=2))):
        with (output / filename).open("x", encoding="utf-8") as stream:
            stream.write(content)
    print(output)
    print(json.dumps(records, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
