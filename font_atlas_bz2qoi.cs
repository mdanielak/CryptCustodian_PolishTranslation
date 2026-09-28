using System;
using System.Buffers.Binary;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using ICSharpCode.SharpZipLib;
using ICSharpCode.SharpZipLib.BZip2;
using UndertaleModLib.Models;
using UndertaleModLib.Util;

// Two bounded stages; NEVER calls GMImage's unbounded Bz2Qoi conversion.
// No game serializer, private-field access, native codec or pixel decoder of our own.
public static class FontAtlasBz2Qoi
{
    public const int MaxBytes = 64 * 1024 * 1024;
    public const string LibrarySha256 = "bb12b0e22b46be6a8e6e498767ca61e8974e5acac6df46e66000afe1dd35fbf5";
    public const string ZipSha256 = "32ea2d0ce3512e74f1c7ad82591fe67e6b8939d76a8a4ff9c93ead030131e71c";
    static readonly Lazy<bool> Reviewed = new Lazy<bool>(() => {
        VerifyAssembly(typeof(GMImage).Assembly, LibrarySha256);
        VerifyAssembly(typeof(BZip2InputStream).Assembly, ZipSha256);
        return true;
    });
    static void VerifyAssembly(Assembly assembly, string expected)
    {
        using (var stream = File.OpenRead(assembly.Location))
            Need(Convert.ToHexString(SHA256.HashData(stream)).Equals(expected, StringComparison.OrdinalIgnoreCase), "UNREVIEWED_CODEC_DLL");
    }
    static void Need(bool condition, string code) => FontBuilderCore.Require(condition, "BZ2QOI_" + code);

    internal static int PixelBytes(int width, int height)
    {
        Need(width > 0 && width <= 4096 && height > 0 && height <= 4096, "DIMENSIONS");
        long length = checked((long)width * height * 4);
        Need(length <= MaxBytes, "OUTPUT_LIMIT");
        return checked((int)length);
    }

    public static byte[] Decode(GMImage image, UndertaleTexturePageItem t, int txtrWidth, int txtrHeight, bool extendedHeader)
    {
        Need(image != null && image.Format == GMImage.ImageFormat.Bz2Qoi, "FORMAT");
        PixelBytes(txtrWidth, txtrHeight); // Missing/old TXTR dimensions fail closed; TPAG is only a subrectangle.
        Need(image.Width == txtrWidth && image.Height == txtrHeight, "TXTR_DIMENSION_MISMATCH");
        FontAtlasUmt.RequireIdentityLayout(t);
        Need(t.SourceWidth > 0 && t.SourceHeight > 0 &&
            (long)t.SourceX + t.SourceWidth <= txtrWidth && (long)t.SourceY + t.SourceHeight <= txtrHeight, "TPAG_BOUNDS");
        Need(Reviewed.Value, "UNREVIEWED_CODEC_DLL");

        // GMImage.ToSpan allocates before its length can be checked (and this DLL
        // reads Position after closing its MemoryStream). Use the public writer
        // ONLY to a counting sink, then an exactly sized, non-expandable memory buffer.
        using (var count = new CountSink(MaxBytes))
        {
            using (var writer = new BinaryWriter(count, Encoding.UTF8, true)) image.WriteToBinaryWriter(writer, extendedHeader);
            byte[] snapshot = new byte[checked((int)count.Length)];
            using (var memory = new MemoryStream(snapshot, true))
            using (var writer = new BinaryWriter(memory, Encoding.UTF8, true))
            {
                image.WriteToBinaryWriter(writer, extendedHeader);
                Need(memory.Position == snapshot.Length, "SNAPSHOT_LENGTH");
            }
            return DecodeEnvelope(snapshot, extendedHeader, txtrWidth, txtrHeight);
        }
    }

    // The production caller owns this snapshot exclusively. Tests use synthetic envelopes.
    internal static byte[] DecodeEnvelope(byte[] bytes, bool extendedHeader, int width, int height)
    {
        int rawLength = PixelBytes(width, height), headerSize = extendedHeader ? 12 : 8;
        Need(bytes != null && bytes.Length <= MaxBytes, "COMPRESSED_LIMIT");
        Need(bytes.Length >= headerSize + 10, "TRUNCATED_ENVELOPE");
        Need(bytes.AsSpan(0, 4).SequenceEqual(new byte[] { 50, 122, 111, 113 }), "OUTER_MAGIC");
        Need(BinaryPrimitives.ReadUInt16LittleEndian(bytes.AsSpan(4, 2)) == width &&
            BinaryPrimitives.ReadUInt16LittleEndian(bytes.AsSpan(6, 2)) == height, "OUTER_DIMENSION_MISMATCH");
        int qoiLimit = (int)Math.Min(MaxBytes, 12L + (long)width * height * 5);
        int declared = extendedHeader ? BinaryPrimitives.ReadInt32LittleEndian(bytes.AsSpan(8, 4)) : -1;
        Need(!extendedHeader || (declared >= 12 && declared <= qoiLimit), "DECLARED_OUTPUT_LIMIT");
        Need(bytes[headerSize] == 'B' && bytes[headerSize + 1] == 'Z' && bytes[headerSize + 2] == 'h' &&
            bytes[headerSize + 3] >= '1' && bytes[headerSize + 3] <= '9', "BZIP_MAGIC");
        Need(bytes.AsSpan(headerSize + 4, 6).SequenceEqual(new byte[] { 0x31, 0x41, 0x59, 0x26, 0x53, 0x59 }), "BZIP_BLOCK_MAGIC");
        Need(Reviewed.Value, "UNREVIEWED_CODEC_DLL");
        byte[] qoi;
        try
        {
            using (var input = new MemoryStream(bytes, headerSize, bytes.Length - headerSize, false))
            using (var bz = new BZip2InputStream(input) { IsStreamOwner = false })
            {
                // BZip2 needs a bounded block workspace, not an unbounded output stream.
                // Inspect the inner header before allocating any QOI payload or pixels.
                byte[] header = new byte[12];
                bz.ReadExactly(header, 0, header.Length);
                Need(header.AsSpan(0, 4).SequenceEqual(new byte[] { 102, 105, 111, 113 }), "QOI_MAGIC");
                int innerWidth = BinaryPrimitives.ReadUInt16LittleEndian(header.AsSpan(4, 2));
                int innerHeight = BinaryPrimitives.ReadUInt16LittleEndian(header.AsSpan(6, 2));
                PixelBytes(innerWidth, innerHeight);
                Need(innerWidth == width && innerHeight == height, "QOI_DIMENSION_MISMATCH");
                uint payload = BinaryPrimitives.ReadUInt32LittleEndian(header.AsSpan(8, 4));
                Need(payload > 0 && 12L + payload <= qoiLimit, "QOI_OUTPUT_LIMIT");
                int length = checked(12 + (int)payload);
                Need(!extendedHeader || declared == length, "DECLARED_LENGTH_MISMATCH");
                qoi = new byte[length];
                header.CopyTo(qoi, 0);
                bz.ReadExactly(qoi, 12, length - 12);
                // One-byte overflow probe, never stored in the output. Forces end/CRC
                // validation. Reject extra blocks, concatenated streams and trailing bytes.
                Need(bz.ReadByte() == -1, "EXCESS_OUTPUT");
                Need(input.Position == input.Length, "TRAILING_COMPRESSED_DATA");
            }
        }
        catch (InvalidDataException) { throw; }
        catch (Exception e) when (e is IOException || e is SharpZipBaseException || e is IndexOutOfRangeException || e is ArgumentException || e is OverflowException)
        { throw new InvalidDataException("BZ2QOI_MALFORMED_BZIP_STREAM", e); }

        ValidateTokens(qoi, width * height);
        // UMT performs the pixel decoding. At this point allocation sizes and the
        // complete opcode framing have been checked, on the same private bytes.
        var raw = GMImage.FromQoi(qoi).ConvertToFormat(GMImage.ImageFormat.RawBgra);
        Need(raw.Format == GMImage.ImageFormat.RawBgra && raw.Width == width && raw.Height == height, "DECODE_DIMENSIONS");
        var span = raw.GetRawImageData();
        Need(span.Length == rawLength, "DECODE_LENGTH");
        return span.ToArray(); // No writable UMT-owned span escapes.
    }

    // Structural scanner only: no color, index-table, differential or pixel decoding.
    // UMT otherwise silently fills missing pixels and ignores surplus tokens/runs.
    internal static void ValidateTokens(byte[] qoi, int pixels)
    {
        int pos = 12, produced = 0;
        while (pos < qoi.Length)
        {
            byte op = qoi[pos++]; int extra = 0, count = 1;
            if ((op & 0xe0) == 0x40) count = (op & 31) + 1;
            else if ((op & 0xe0) == 0x60)
            {
                Need(pos < qoi.Length, "TRUNCATED_QOI_TOKEN");
                count = ((op & 31) << 8 | qoi[pos++]) + 33;
            }
            else if ((op & 0xe0) == 0xc0) extra = 1;
            else if ((op & 0xf0) == 0xe0) extra = 2;
            else if ((op & 0xf0) == 0xf0)
                extra = ((op >> 3) & 1) + ((op >> 2) & 1) + ((op >> 1) & 1) + (op & 1);
            Need(extra <= qoi.Length - pos, "TRUNCATED_QOI_TOKEN");
            pos += extra;
            Need(count <= pixels - produced, "QOI_PIXEL_OVERRUN");
            produced += count;
        }
        Need(produced == pixels, "QOI_PIXEL_UNDERRUN");
    }

    internal sealed class CountSink : Stream
    {
        readonly int limit; long length;
        public CountSink(int max) { limit = max; }
        void Add(int count) { Need(count >= 0 && count <= limit - length, "COMPRESSED_LIMIT"); length += count; }
        public override void Write(byte[] buffer, int offset, int count) { Add(count); }
        public override void Write(ReadOnlySpan<byte> buffer) { Add(buffer.Length); }
        public override void WriteByte(byte value) { Add(1); }
        public override bool CanRead => false;
        public override bool CanSeek => false;
        public override bool CanWrite => true;
        public override long Length => length;
        public override long Position { get => length; set => throw new NotSupportedException(); }
        public override void Flush() { }
        public override int Read(byte[] buffer, int offset, int count) => throw new NotSupportedException();
        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();
        public override void SetLength(long value) => throw new NotSupportedException();
    }
}
