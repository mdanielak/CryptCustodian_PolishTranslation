using System;
using System.Buffers.Binary;
using System.IO;
using System.Linq;
using ICSharpCode.SharpZipLib.BZip2;
using UndertaleModLib.Models;
using UndertaleModLib.Util;

internal static class Bz2QoiTests
{
    static void Check(bool ok) { if (!ok) throw new Exception("codec assertion"); }
    static void Refuse(Action action, string reason = "BZ2QOI_")
    {
        try { action(); }
        catch (InvalidDataException e) { Check(e.Message.Contains(reason)); return; }
        throw new Exception("Expected " + reason);
    }
    static void U16(byte[] bytes, int offset, int value) => BinaryPrimitives.WriteUInt16LittleEndian(bytes.AsSpan(offset, 2), (ushort)value);
    static void U32(byte[] bytes, int offset, uint value) => BinaryPrimitives.WriteUInt32LittleEndian(bytes.AsSpan(offset, 4), value);
    static byte[] Qoi(int w, int h, params byte[] tokens)
    {
        var q = new byte[12 + tokens.Length]; new byte[] { 102, 105, 111, 113 }.CopyTo(q, 0);
        U16(q, 4, w); U16(q, 6, h); U32(q, 8, (uint)tokens.Length); tokens.CopyTo(q, 12); return q;
    }
    static byte[] Envelope(byte[] qoi, int w = 2, int h = 1, bool extended = true, int? declared = null, int block = 9)
    {
        using (var output = new MemoryStream())
        {
            using (var writer = new BinaryWriter(output, System.Text.Encoding.UTF8, true))
            {
                writer.Write(new byte[] { 50, 122, 111, 113 }); writer.Write((ushort)w); writer.Write((ushort)h);
                if (extended) writer.Write(declared ?? qoi.Length);
            }
            using (var input = new MemoryStream(qoi, false)) BZip2.Compress(input, output, false, block);
            return output.ToArray();
        }
    }
    static byte[] FixtureQoi() => Qoi(2, 1, 0xff, 33, 22, 11, 127, 0xff, 66, 55, 44, 0);
    static byte[] Decode(byte[] b, bool extended = true, int w = 2, int h = 1) => FontAtlasBz2Qoi.DecodeEnvelope(b, extended, w, h);
    static UndertaleTexturePageItem Tpag(int w = 2, int h = 1) => new UndertaleTexturePageItem {
        SourceWidth = (ushort)w, TargetWidth = (ushort)w, BoundingWidth = (ushort)w,
        SourceHeight = (ushort)h, TargetHeight = (ushort)h, BoundingHeight = (ushort)h };

    public static void Run(Action<string, Action> test)
    {
        foreach (bool extended in new[] { false, true })
        foreach (int block in new[] { 1, 9 })
            test("Bz2Qoi positive header=" + extended + " block=" + block, () => {
                var input = Envelope(FixtureQoi(), extended: extended, block: block); var before = (byte[])input.Clone();
                var a = Decode(input, extended); var b = Decode(input, extended);
                Check(a.SequenceEqual(new byte[] { 11, 22, 33, 127, 44, 55, 66, 0 }) && a.SequenceEqual(b));
                Check(input.SequenceEqual(before)); a[0] = 255; Check(b[0] == 11);
                Check(FontAtlasCore.FromBgra(2, 1, b).Pixels.SequenceEqual(new byte[] { 33, 22, 11, 127, 66, 55, 44, 0 }));
            });
        test("Bz2Qoi public GMImage adapter snapshots and preserves ownership", () => {
            byte[] envelope = Envelope(FixtureQoi()); byte[] payload = envelope.Skip(12).ToArray(); var before = (byte[])payload.Clone();
            var image = GMImage.FromBz2Qoi(payload, 2, 1, FixtureQoi().Length);
            var a = FontAtlasBz2Qoi.Decode(image, Tpag(), 2, 1, true);
            a[0] = 255;
            Check(FontAtlasBz2Qoi.Decode(image, Tpag(), 2, 1, true)[0] == 11);
            Check(payload.SequenceEqual(before) && image.Format == GMImage.ImageFormat.Bz2Qoi);
            // FromBz2Qoi itself aliases its input. Our returned pixel copy does not.
            payload[0] = 0;
            Refuse(() => FontAtlasBz2Qoi.Decode(image, Tpag(), 2, 1, true), "BZIP_MAGIC");
        });
        test("Bz2Qoi independent known pixels cover GM QOI token families", () => {
            // color, index (hash 10^20^30^40=40), diff8/16/24 (zero), run8, run16.
            byte[] tokens = { 0xff, 10, 20, 30, 40, 40, 0x80, 0xc0, 0, 0xe0, 0, 0, 0x41, 0x60, 0 };
            byte[] raw = Decode(Envelope(Qoi(40, 1, tokens), 40), w: 40);
            Check(raw.Length == 160);
            for (int i = 0; i < raw.Length; i += 4) Check(raw.AsSpan(i, 4).SequenceEqual(new byte[] { 30, 20, 10, 40 }));
        });
        test("Bz2Qoi UMT encoder to bounded decoder pixel round-trip", () => {
            var image = new GMImage(17, 13); var random = new Random(274);
            byte[] pixels = new byte[17 * 13 * 4]; random.NextBytes(pixels); pixels.CopyTo(image.GetRawImageData());
            var compressed = image.ConvertToBz2Qoi();
            Check(FontAtlasBz2Qoi.Decode(compressed, Tpag(17, 13), 17, 13, true).SequenceEqual(pixels));
            Check(image.GetRawImageData().SequenceEqual(pixels));
        });
        test("Bz2Qoi multiple BZip2 blocks round-trip", () => {
            var image = new GMImage(256, 256); byte[] pixels = new byte[256 * 256 * 4];
            new Random(42).NextBytes(pixels); pixels.CopyTo(image.GetRawImageData());
            byte[] qoi = QoiConverter.GetArrayFromImage(image); Check(qoi.Length > 300000);
            Check(Decode(Envelope(qoi, 256, 256, block: 1), w: 256, h: 256).SequenceEqual(pixels));
        });
        test("RawBgra ConvertToFormat returns same image and writable owned span", () => {
            var image = new GMImage(1, 1); var raw = image.ConvertToFormat(GMImage.ImageFormat.RawBgra);
            Check(ReferenceEquals(image, raw)); raw.GetRawImageData()[0] = 91; Check(image.GetRawImageData()[0] == 91);
        });
        test("QOI wrappers alias bytes but decoded raw is independent", () => {
            byte[] q = FixtureQoi(); var image = GMImage.FromQoi(q); q[13] = 99;
            var raw = image.ConvertToFormat(GMImage.ImageFormat.RawBgra);
            Check(raw.GetRawImageData()[2] == 99); raw.GetRawImageData()[2] = 0; Check(q[13] == 99);
        });
        foreach (bool extended in new[] { false, true })
            test("Bz2Qoi truncation at EVERY envelope byte header=" + extended, () => {
                var valid = Envelope(FixtureQoi(), extended: extended);
                for (int cut = 0; cut < valid.Length; cut++) Refuse(() => Decode(valid.Take(cut).ToArray(), extended));
            });
        test("Bz2Qoi invalid outer magic", () => { var b = Envelope(FixtureQoi()); b[0] ^= 1; Refuse(() => Decode(b), "OUTER_MAGIC"); });
        test("Bz2Qoi outer dimension mismatch", () => { var b = Envelope(FixtureQoi()); U16(b, 4, 3); Refuse(() => Decode(b), "OUTER_DIMENSION_MISMATCH"); });
        foreach (int index in new[] { 12, 13, 14, 15, 16, 21 })
            test("Bz2Qoi invalid BZip2 magic byte=" + index, () => {
                var b = Envelope(FixtureQoi()); b[index] = 0; Refuse(() => Decode(b), index < 16 ? "BZIP_MAGIC" : "BZIP_BLOCK_MAGIC");
            });
        test("Bz2Qoi blocksize 0 and 10 refused", () => {
            foreach (byte size in new[] { (byte)'0', (byte)':' }) { var b = Envelope(FixtureQoi()); b[15] = size; Refuse(() => Decode(b), "BZIP_MAGIC"); }
        });
        test("Bz2Qoi bad block CRC", () => { var b = Envelope(FixtureQoi()); b[22] ^= 1; Refuse(() => Decode(b), "MALFORMED_BZIP_STREAM"); });
        test("Bz2Qoi bad combined CRC", () => { var b = Envelope(FixtureQoi()); b[b.Length - 3] ^= 1; Refuse(() => Decode(b), "MALFORMED_BZIP_STREAM"); });
        test("Bz2Qoi malformed coding tables", () => {
            var b = Envelope(FixtureQoi()); for (int i = 26; i < b.Length - 10; i++) b[i] = 0xff;
            Refuse(() => Decode(b), "MALFORMED_BZIP_STREAM");
        });
        test("Bz2Qoi trailing garbage and concatenated stream refused", () => {
            var b = Envelope(FixtureQoi());
            Refuse(() => Decode(b.Concat(new byte[] { 0 }).ToArray()), "TRAILING_COMPRESSED_DATA");
            Refuse(() => Decode(b.Concat(b.Skip(12)).ToArray()), "TRAILING_COMPRESSED_DATA");
        });
        test("Bz2Qoi truncated inner QOI header", () => Refuse(() => Decode(Envelope(new byte[] { 102, 105, 111 }, declared: 12)), "MALFORMED_BZIP_STREAM"));
        test("Bz2Qoi standard qoif header not GM fio q", () => {
            var q = FixtureQoi(); new byte[] { 113, 111, 105, 102 }.CopyTo(q, 0); Refuse(() => Decode(Envelope(q)), "QOI_MAGIC");
        });
        foreach (int dim in new[] { 0, 4097, 16384, 32768, 65535 })
            test("Bz2Qoi oversized/zero inner dimension=" + dim, () => {
                var q = FixtureQoi(); U16(q, 4, dim); Refuse(() => Decode(Envelope(q)), "DIMENSIONS");
                q = FixtureQoi(); U16(q, 6, dim); Refuse(() => Decode(Envelope(q)), "DIMENSIONS");
            });
        test("Bz2Qoi inner dimensions mismatch before pixels", () => {
            var q = FixtureQoi(); U16(q, 4, 1); Refuse(() => Decode(Envelope(q)), "QOI_DIMENSION_MISMATCH");
        });
        foreach (uint length in new[] { 0u, 11u, 67108865u, 0x7fffffffu, 0xfffffff4u, uint.MaxValue })
            test("Bz2Qoi inner oversized/overflow payload=" + length, () => {
                var q = FixtureQoi(); U32(q, 8, length); Refuse(() => Decode(Envelope(q)), "QOI_OUTPUT_LIMIT");
            });
        foreach (int length in new[] { -1, 0, 11, 67108865, int.MaxValue, int.MinValue })
            test("Bz2Qoi outer untrusted size=" + length, () => Refuse(() => Decode(Envelope(FixtureQoi(), declared: length)), "DECLARED_OUTPUT_LIMIT"));
        test("Bz2Qoi outer/inner payload size mismatch", () => Refuse(() => Decode(Envelope(FixtureQoi(), declared: 21)), "DECLARED_LENGTH_MISMATCH"));
        test("Bz2Qoi missing QOI body bytes", () => Refuse(() => Decode(Envelope(FixtureQoi().Take(21).ToArray(), declared: 22)), "MALFORMED_BZIP_STREAM"));
        test("Bz2Qoi compressed expansion refused at expected output plus one byte", () => {
            // Deliberately misleading small outer/inner sizes with a compressible 1 MiB suffix.
            var q = new byte[1024 * 1024]; FixtureQoi().CopyTo(q, 0);
            Refuse(() => Decode(Envelope(q, declared: 22)), "EXCESS_OUTPUT");
        });
        test("Bz2Qoi opcode operand truncation", () => {
            foreach (var token in new[] { new byte[] { 0xff, 1, 2, 3 }, new byte[] { 0x60 }, new byte[] { 0xc0 }, new byte[] { 0xe0, 0 } })
                Refuse(() => Decode(Envelope(Qoi(2, 1, token))), "TRUNCATED_QOI_TOKEN");
        });
        test("Bz2Qoi framing covers all 256 opcodes and every operand truncation", () => {
            for (int op = 0; op < 256; op++)
            {
                int operands = op < 96 ? 0 : op < 128 ? 1 : op < 192 ? 0 : op < 224 ? 1 : op < 240 ? 2 :
                    Convert.ToString(op & 15, 2).Count(bit => bit == '1');
                byte[] tokens = new byte[1 + operands]; tokens[0] = (byte)op;
                int pixels = op < 64 || op >= 128 ? 1 : op < 96 ? op - 64 + 1 : (op - 96) * 256 + 33;
                FontAtlasBz2Qoi.ValidateTokens(Qoi(1, 1, tokens), pixels);
                Refuse(() => FontAtlasBz2Qoi.ValidateTokens(Qoi(1, 1, tokens), pixels - 1), "QOI_PIXEL_OVERRUN");
                Refuse(() => FontAtlasBz2Qoi.ValidateTokens(Qoi(1, 1, tokens), pixels + 1), "QOI_PIXEL_UNDERRUN");
                for (int cut = 1; cut < tokens.Length; cut++)
                    Refuse(() => FontAtlasBz2Qoi.ValidateTokens(Qoi(1, 1, tokens.Take(cut).ToArray()), pixels), "TRUNCATED_QOI_TOKEN");
            }
        });
        test("Bz2Qoi missing pixels refused instead of UMT implicit fill", () => Refuse(() => Decode(Envelope(Qoi(2, 1, 0x40))), "QOI_PIXEL_UNDERRUN"));
        test("Bz2Qoi surplus runs and tokens refused instead of UMT ignoring", () => {
            foreach (var token in new[] { new byte[] { 0x42 }, new byte[] { 0x60, 0 }, new byte[] { 0x41, 0 } })
                Refuse(() => Decode(Envelope(Qoi(2, 1, token))), "QOI_PIXEL_OVERRUN");
        });
        test("Bz2Qoi TXTR and TPAG gates before any BZip2 decode", () => {
            var image = GMImage.FromBz2Qoi(new byte[0], 2, 1, 22);
            Refuse(() => FontAtlasBz2Qoi.Decode(image, Tpag(), 0, 0, true), "DIMENSIONS");
            Refuse(() => FontAtlasBz2Qoi.Decode(image, Tpag(), 3, 1, true), "TXTR_DIMENSION_MISMATCH");
            var t = Tpag(); t.SourceX = ushort.MaxValue;
            Refuse(() => FontAtlasBz2Qoi.Decode(image, t, 2, 1, true), "TPAG_BOUNDS");
            t = Tpag(); t.SourceY = 1;
            Refuse(() => FontAtlasBz2Qoi.Decode(image, t, 2, 1, true), "TPAG_BOUNDS");
            t = Tpag(); t.TargetX = 1;
            Refuse(() => FontAtlasBz2Qoi.Decode(image, t, 2, 1, true), "TPAG_NONIDENTITY_LAYOUT");
        });
        test("Bz2Qoi counting sink exact 64 MiB and plus one without large allocation", () => {
            using (var sink = new FontAtlasBz2Qoi.CountSink(FontAtlasBz2Qoi.MaxBytes))
            {
                byte[] chunk = new byte[8192];
                for (int i = 0; i < FontAtlasBz2Qoi.MaxBytes / chunk.Length; i++) sink.Write(chunk, 0, chunk.Length);
                Check(sink.Length == FontAtlasBz2Qoi.MaxBytes);
                Refuse(() => sink.WriteByte(0), "COMPRESSED_LIMIT"); Check(sink.Length == FontAtlasBz2Qoi.MaxBytes);
            }
        });
        test("Bz2Qoi dimensions and multiplication checked before allocation", () => {
            Check(FontAtlasBz2Qoi.PixelBytes(4096, 4096) == FontAtlasBz2Qoi.MaxBytes);
            foreach (int n in new[] { -1, 0, 4097, int.MaxValue, int.MinValue })
            { Refuse(() => FontAtlasBz2Qoi.PixelBytes(n, 4096)); Refuse(() => FontAtlasBz2Qoi.PixelBytes(4096, n)); }
        });
        test("Bz2Qoi actual raw output exactly 64 MiB", () => {
            int left = 4096 * 4096; using (var tokens = new MemoryStream())
            {
                while (left > 0)
                {
                    int count = Math.Min(left, 8224); left -= count;
                    if (count <= 32) tokens.WriteByte((byte)(0x40 | (count - 1)));
                    else { int n = count - 33; tokens.WriteByte((byte)(0x60 | (n >> 8))); tokens.WriteByte((byte)n); }
                }
                byte[] pixels = Decode(Envelope(Qoi(4096, 4096, tokens.ToArray()), 4096, 4096), w: 4096, h: 4096);
                Check(pixels.Length == FontAtlasBz2Qoi.MaxBytes);
                Check(pixels.AsSpan(0, 4).SequenceEqual(new byte[] { 0, 0, 0, 255 }));
                Check(pixels.AsSpan(pixels.Length - 4, 4).SequenceEqual(new byte[] { 0, 0, 0, 255 }));
            }
        });
    }
}
