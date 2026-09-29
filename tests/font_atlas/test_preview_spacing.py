"""Synthetic renderer regressions; no files, game, fonts or dependencies."""
import struct
import unittest
import zlib
from preview_spacing import layout, png, render


def glyph(offset=0, shift=1, kern=None):
    return dict(w=3, h=1, pixels=bytes((255, 255, 255, 255)) * 3,
                offset=offset, shift=shift, kern=kern or {})


class PreviewTests(unittest.TestCase):
    def test_offset_does_not_change_pen(self):
        gs = {'ł': glyph(offset=2), 'l': glyph()}
        placed, pen = layout('łl', gs)
        self.assertEqual([x for x, _ in placed], [2, 1])
        self.assertEqual(pen, 2)

    def test_incoming_kerning_before_offset(self):
        gs = {'i': glyph(shift=4), 'ł': glyph(offset=-1, kern={ord('i'): -2})}
        placed, pen = layout('ił', gs)
        self.assertEqual([x for x, _ in placed], [0, 1])
        self.assertEqual(pen, 3)

    def test_render_does_not_clip_to_advance(self):
        canvas = bytearray(bytes((0, 0, 0, 255)) * 8)
        render(canvas, 8, 1, 'ł', {'ł': glyph(offset=1, shift=1)}, 0, 0)
        self.assertEqual([canvas[x * 4] for x in range(8)], [0, 255, 255, 255, 0, 0, 0, 0])

    def test_png_crc_and_rgba_roundtrip(self):
        raw = bytes(range(24))
        encoded = png(raw, 3, 2)
        self.assertEqual(encoded[:8], b'\x89PNG\r\n\x1a\n')
        offset, idat = 8, bytearray()
        while offset < len(encoded):
            size = struct.unpack_from('>I', encoded, offset)[0]
            tag = encoded[offset + 4:offset + 8]
            data = encoded[offset + 8:offset + 8 + size]
            crc = struct.unpack_from('>I', encoded, offset + 8 + size)[0]
            self.assertEqual(crc, zlib.crc32(tag + data))
            if tag == b'IDAT':
                idat.extend(data)
            offset += size + 12
        scan = zlib.decompress(idat)
        self.assertEqual(scan[0], 0)
        self.assertEqual(scan[13], 0)
        self.assertEqual(scan[1:13] + scan[14:26], raw)


if __name__ == '__main__':
    unittest.main()
