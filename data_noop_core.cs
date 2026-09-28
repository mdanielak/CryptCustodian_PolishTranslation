using System;
using System.Buffers.Binary;
using System.Collections;
using System.Collections.Generic;
using System.Collections.Immutable;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace CCDataNoop;

public sealed class GateRefusal : Exception { public GateRefusal(string message) : base(message) { } }

// No mutable buffer escapes. The stream is non-writable and does not expose its buffer.
public sealed class FrozenBytes
{
    readonly byte[] bytes;
    FrozenBytes(byte[] owned) { bytes = owned; Sha256 = Hash(bytes); }
    public static FrozenBytes CopyOf(ReadOnlySpan<byte> input) => new(input.ToArray());
    public static FrozenBytes ReadFile(string path)
    {
        using var s = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        NoopPolicy.Require(s.Length > 0 && s.Length <= 1024L * 1024 * 1024, "INPUT_SIZE");
        var b = new byte[checked((int)s.Length)]; s.ReadExactly(b);
        NoopPolicy.Require(s.ReadByte() == -1, "INPUT_GREW"); return new FrozenBytes(b);
    }
    public int Length => bytes.Length;
    public ReadOnlySpan<byte> Span => bytes;
    public string Sha256 { get; }
    public Stream OpenRead() => new MemoryStream(bytes, 0, bytes.Length, false, false);
    public void RequireHash(string expected) => NoopPolicy.Require(Sha256 == expected, "SOURCE_SHA256 expected=" + expected + " actual=" + Sha256);
    public static string Hash(ReadOnlySpan<byte> data) => Convert.ToHexString(SHA256.HashData(data)).ToLowerInvariant();
}

public static class NoopPolicy
{
    public static string GameRoot => ConfiguredPath("CC_GAME_ROOT");
    public static string Source => Path.Combine(GameRoot, "data.win");
    public const string SourceSha256 = "15e2c8ef57f4c5f599589b5d15021281a11b73757be54d0bbf739d57dd280b99";
    public static string TempRoot => ConfiguredPath("CC_OUTPUT_ROOT", Path.TrimEndingDirectorySeparator(Path.GetTempPath()));
    public static string ConfiguredPath(string variable, string? fallback = null)
    {
        string? path = Environment.GetEnvironmentVariable(variable) ?? fallback;
        Require(!string.IsNullOrWhiteSpace(path), "CONFIG_REQUIRED " + variable);
        Require(Regex.IsMatch(path!, @"\A[A-Za-z]:\\") && !path!.Contains('/') &&
            path.Split('\\').Skip(1).All(p => p.Length > 0 && p != "." && p != ".." &&
                !p.EndsWith('.') && !p.EndsWith(' ') && !p.Any(char.IsControl) &&
                !Regex.IsMatch(p, @"\A(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|\z)", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant) &&
                p.IndexOfAny(new[] { ':', '*', '?', '"', '<', '>', '|' }) < 0),
            "CONFIG_PATH " + variable);
        return path!;
    }
    public const string UmtSha256 = "bb12b0e22b46be6a8e6e498767ca61e8974e5acac6df46e66000afe1dd35fbf5";
    public const string UnderanalyzerSha256 = "13866effb6db10aa0c7fea1b9f017bb362e8b9e1266295896bebcd920c3571fa";
    public static void Require(bool ok, string message) { if (!ok) throw new GateRefusal(message); }
    public static void Arguments(string[] args) => Require(args.SequenceEqual(new[] { "--round-trip" }), "ARGUMENTS: only --round-trip; configure paths through environment, no CLI -o");
    public static void OutputPath(string path) => Require(Regex.IsMatch(path,
        "\\A" + Regex.Escape(TempRoot) + @"\\cc-data-noop-[0-9]{3,}\\release\z", RegexOptions.CultureInvariant), "OUTPUT_PATH");
    public static void BeforeWrite(bool complete, int rawChunks, int modelChunks, bool collections)
    {
        Require(complete, "SNAPSHOT_INCOMPLETE"); Require(rawChunks == 30 && modelChunks == 30, "CHUNK_COVERAGE");
        Require(collections, "COLLECTION_COVERAGE");
    }
    public static void FatalWarning(string warning, bool important) => throw new GateRefusal("UMT_WARNING important=" + important + ": " + warning);
    // No semantic allowlist; in particular GMS2RandomUID is not ignored.
    public static bool CanAdvance(long semanticDifferences, long binaryDifferences) => semanticDifferences == 0 && binaryDifferences == 0;
}

public sealed record GraphRoot(string Name, object? Value);
public sealed record SnapshotRow(string Path, string Value);
public sealed record Difference(string Path, string? Before, string? After);
public sealed record SnapshotDiff(long Total, ImmutableArray<Difference> Items, bool Truncated);

// Pure, detached, field-only graph traversal. No model getters/ToString/Serialize/Clone.
// All reachable instance fields are visited, including private/base fields and CachedId.
// Framework collections have explicit logical adapters: no unused capacity/hash buckets.
// Unknown managed/native/framework types fail closed, never yield a partial snapshot.
public sealed class GraphSnapshot
{
    public ImmutableArray<SnapshotRow> Rows { get; }
    public ImmutableArray<string> Types { get; }
    public string Sha256 { get; }
    GraphSnapshot(ImmutableArray<SnapshotRow> rows, IEnumerable<string> types)
    {
        Rows = rows; Types = types.Order(StringComparer.Ordinal).ToImmutableArray();
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        foreach (var r in rows)
        {
            // Length framing, not delimiter-dependent concatenation.
            foreach (string text in new[] { r.Path, r.Value })
            {
                byte[] b = Encoding.UTF8.GetBytes(text); hash.AppendData(BitConverter.GetBytes(b.Length)); hash.AppendData(b);
            }
        }
        Sha256 = Convert.ToHexString(hash.GetHashAndReset()).ToLowerInvariant();
    }
    public SnapshotDiff Diff(GraphSnapshot after, int limit)
    {
        NoopPolicy.Require(limit > 0 && limit <= 1024, "DIFF_LIMIT");
        int a = 0, b = 0; long count = 0; var items = ImmutableArray.CreateBuilder<Difference>();
        while (a < Rows.Length || b < after.Rows.Length)
        {
            var left = a < Rows.Length ? Rows[a] : null; var right = b < after.Rows.Length ? after.Rows[b] : null;
            int cmp = left == null ? 1 : right == null ? -1 : string.CompareOrdinal(left.Path, right.Path);
            if (cmp == 0 && left!.Value == right!.Value) { a++; b++; continue; }
            count++;
            if (items.Count < limit) items.Add(new Difference(cmp <= 0 ? left!.Path : right!.Path,
                cmp <= 0 ? Bound(left!.Value) : null, cmp >= 0 ? Bound(right!.Value) : null));
            if (cmp <= 0) a++; if (cmp >= 0) b++;
        }
        return new(count, items.ToImmutable(), count > items.Count);
    }
    static string Bound(string text) => text.Length <= 512 ? text : text[..512] + "... [value truncated; full value in snapshot]";
    static string TypeName(Type t) => t.FullName ?? throw new GateRefusal("UNNAMED_TYPE");
    static string Utf16(string s)
    {
        var bytes = new byte[checked(s.Length * 2)];
        for (int i = 0; i < s.Length; i++) BinaryPrimitives.WriteUInt16LittleEndian(bytes.AsSpan(2 * i), s[i]);
        return "utf16le:" + Convert.ToBase64String(bytes);
    }
    static string? Scalar(object value)
    {
        Type t = value.GetType();
        if (value is string s) return Utf16(s);
        if (value is float f) return "f32:" + BitConverter.SingleToUInt32Bits(f).ToString("x8");
        if (value is double d) return "f64:" + BitConverter.DoubleToUInt64Bits(d).ToString("x16");
        if (value is decimal dec) return "decimal:" + string.Join(",", decimal.GetBits(dec).Select(x => x.ToString("x8")));
        if (value is char c) return "char:" + ((ushort)c).ToString("x4");
        if (value is Guid g) return "guid:" + g.ToString("D");
        if (value is Type type) return "type:" + type.Assembly.GetName().Name + ":" + TypeName(type);
        if (value is MemberInfo member)
        {
            try { return "member:" + member.Module.ModuleVersionId + ":" + member.MetadataToken + ":" + member.DeclaringType?.FullName + ":" + member.Name; }
            catch (Exception e) { throw new GateRefusal("UNSUPPORTED_METADATA: " + e.Message); }
        }
        if (t.IsEnum) return TypeName(t) + ":" + Convert.ChangeType(value, Enum.GetUnderlyingType(t), CultureInfo.InvariantCulture);
        if (value is IntPtr || value is UIntPtr) throw new GateRefusal("NATIVE_POINTER");
        if (t.IsPrimitive) return TypeName(t) + ":" + Convert.ToString(value, CultureInfo.InvariantCulture);
        return null;
    }
    static FieldInfo[] Fields(Type t) => t.GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly)
        .OrderBy(f => f.Name, StringComparer.Ordinal).ToArray();
    static bool Generic(Type t, Type definition) => t.IsGenericType && t.GetGenericTypeDefinition() == definition;
    static bool SafeFrameworkFields(Type t)
    {
        if (t.Assembly == typeof(List<>).Assembly && t.Namespace == "System.Collections.Generic" && TypeName(t).Contains("EqualityComparer")) return true;
        if (t.Assembly == typeof(object).Assembly && t.Namespace == "System" &&
            new[] { "StringComparer", "OrdinalComparer", "OrdinalCaseSensitiveComparer", "OrdinalIgnoreCaseComparer" }.Contains(t.Name)) return true;
        if ((t.Assembly == typeof(System.Collections.ObjectModel.ObservableCollection<>).Assembly || t.Assembly == typeof(System.Collections.ObjectModel.Collection<>).Assembly) &&
            t.Namespace == "System.Collections.ObjectModel" &&
            (Generic(t, typeof(System.Collections.ObjectModel.Collection<>)) || Generic(t, typeof(System.Collections.ObjectModel.ObservableCollection<>)) || t.Name == "SimpleMonitor")) return true;
        if (t.Assembly == typeof(System.Drawing.Rectangle).Assembly && t.Namespace == "System.Drawing" && t.IsValueType) return true;
        return t.Assembly == typeof(object).Assembly && t.IsValueType && t.Namespace == "System" && t.Name.StartsWith("ValueTuple`", StringComparison.Ordinal);
    }
    public static GraphSnapshot Capture(IEnumerable<GraphRoot> inputRoots, IEnumerable<Assembly> trustedModelAssemblies, int maxRows = 8000000)
    {
        var allowed = trustedModelAssemblies.ToHashSet();
        var roots = inputRoots.ToArray();
        NoopPolicy.Require(roots.Select(r => r.Name).Distinct(StringComparer.Ordinal).Count() == roots.Length, "DUPLICATE_ROOT");
        var rows = new List<SnapshotRow>(); var types = new HashSet<string>(StringComparer.Ordinal);
        var known = new Dictionary<object, int>(ReferenceEqualityComparer.Instance);
        var origins = new Dictionary<string, string>(StringComparer.Ordinal);
        var fieldCache = new Dictionary<Type, FieldInfo[]>();
        var queue = new Queue<(object value, string path, string origin)>(); int id = 0;
        string Expand(string path)
        {
            int depth = 0;
            while (path.StartsWith('@') && path.Length >= 9 && origins.TryGetValue(path[..9], out string? parent))
            {
                path = parent + path[9..];
                if (++depth > 2048) throw new GateRefusal("ORIGIN_DEPTH");
            }
            return path;
        }
        void Add(string path, string value)
        { NoopPolicy.Require(rows.Count < maxRows, "SNAPSHOT_LIMIT at " + path); rows.Add(new(path, value)); }
        void Visit(string path, object? value)
        {
            if (value == null) { Add(path, "null"); return; }
            string? scalar;
            try { scalar = Scalar(value); } catch (GateRefusal e) { throw new GateRefusal(e.Message + " at " + path); }
            if (scalar != null) { Add(path, scalar); return; }
            Type t = value.GetType();
            if (!t.IsValueType && known.TryGetValue(value, out int existing)) { Add(path, "ref:@" + existing.ToString("D8")); return; }
            int next = id++; if (!t.IsValueType) known.Add(value, next);
            string node = "@" + next.ToString("D8"); origins.Add(node, path); Add(path, "ref:" + node);
            Add(node + "/type", TypeName(t)); Add(node + "/origin", path); types.Add(TypeName(t)); queue.Enqueue((value, node, path));
        }
        foreach (var r in roots) Visit("root/" + r.Name, r.Value);
        while (queue.Count > 0)
        {
            var (value, path, origin) = queue.Dequeue(); Type t = value.GetType();
            if (value is Array array)
            {
                Add(path + "/shape", string.Join(",", Enumerable.Range(0, array.Rank).Select(i => array.GetLowerBound(i) + ":" + array.GetLength(i))));
                Type element = t.GetElementType()!;
                if (element.IsPrimitive && element != typeof(IntPtr) && element != typeof(UIntPtr))
                {
                    int length = Buffer.ByteLength(array); var bytes = new byte[length]; Buffer.BlockCopy(array, 0, bytes, 0, length);
                    Add(path + "/payload", "bytes:" + length + ";sha256:" + FrozenBytes.Hash(bytes));
                }
                else { int i = 0; foreach (object? item in array) Visit(path + "/item/" + (i++).ToString("D8"), item); }
                continue;
            }
            if (Generic(t, typeof(List<>)))
            {
                var list = (IList)value; Add(path + "/count", list.Count.ToString(CultureInfo.InvariantCulture));
                for (int i = 0; i < list.Count; i++) Visit(path + "/item/" + i.ToString("D8"), list[i]);
                continue;
            }
            if (Generic(t, typeof(Dictionary<,>)))
            {
                var dict = (IDictionary)value; Add(path + "/count", dict.Count.ToString(CultureInfo.InvariantCulture));
                // Actual comparer is traversed too; custom comparer state is never ignored.
                Visit(path + "/comparer", t.GetField("_comparer", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(value));
                int i = 0;
                foreach (DictionaryEntry entry in dict)
                {
                    string p = path + "/entry/" + (i++).ToString("D8"); Visit(p + "/key", entry.Key); Visit(p + "/value", entry.Value);
                }
                continue;
            }
            if (value is Delegate del)
            {
                // Never invoke callbacks. Preserve method, target identity, and invocation order.
                var calls = del.GetInvocationList(); Add(path + "/count", calls.Length.ToString(CultureInfo.InvariantCulture));
                for (int i = 0; i < calls.Length; i++)
                { Visit(path + "/call/" + i + "/method", calls[i].Method); Visit(path + "/call/" + i + "/target", calls[i].Target); }
                continue;
            }
            for (Type? current = t; current != null && current != typeof(object) && current != typeof(ValueType); current = current.BaseType)
            {
                if (!allowed.Contains(current.Assembly) && !SafeFrameworkFields(current))
                    throw new GateRefusal("UNSUPPORTED_TYPE " + TypeName(current) + " at " + path + " (origin=" + Expand(origin) + ")");
                if (!fieldCache.TryGetValue(current, out var fields)) fieldCache[current] = fields = Fields(current);
                foreach (FieldInfo field in fields)
                {
                    string p = path + "/field/" + TypeName(current) + "/" + field.Name;
                    NoopPolicy.Require(!field.FieldType.IsPointer && !field.FieldType.IsByRefLike && !field.FieldType.IsByRef, "UNSAFE_FIELD " + p);
                    try { Visit(p, field.GetValue(value)); }
                    catch (GateRefusal) { throw; }
                    catch (Exception e) { throw new GateRefusal("FIELD_READ_FAILED " + p + ": " + e.GetType().Name + ": " + e.Message); }
                }
            }
        }
        return new GraphSnapshot(rows.OrderBy(r => r.Path, StringComparer.Ordinal).ToImmutableArray(), types);
    }
}

public sealed record ChunkLayout(string Name, int Offset, int Length, string Sha256, int EndMod16, int TailZeroBytes);
public sealed record ByteRange(int Offset, int Length, string BeforeHex, string AfterHex);
public sealed record BinaryDiff(long ChangedBytes, long RangeCount, ImmutableArray<ByteRange> Ranges, bool Truncated, bool SameSize);
public sealed class BinaryLayout
{
    public int FormLength { get; }
    public ImmutableArray<ChunkLayout> Chunks { get; }
    BinaryLayout(int length, ImmutableArray<ChunkLayout> chunks) { FormLength = length; Chunks = chunks; }
    public static BinaryLayout Parse(FrozenBytes input)
    {
        ReadOnlySpan<byte> b = input.Span;
        NoopPolicy.Require(b.Length >= 8 && b[..4].SequenceEqual("FORM"u8), "FORM_MAGIC");
        NoopPolicy.Require(BinaryPrimitives.ReadUInt32LittleEndian(b[4..]) == b.Length - 8, "FORM_LENGTH");
        var chunks = ImmutableArray.CreateBuilder<ChunkLayout>(); var names = new HashSet<string>();
        int p = 8;
        while (p < b.Length)
        {
            NoopPolicy.Require(p <= b.Length - 8, "CHUNK_HEADER");
            string name = Encoding.ASCII.GetString(b.Slice(p, 4));
            NoopPolicy.Require(Regex.IsMatch(name, "\\A[A-Z0-9]{4}\\z"), "CHUNK_NAME");
            NoopPolicy.Require(names.Add(name), "DUPLICATE_CHUNK " + name);
            uint n = BinaryPrimitives.ReadUInt32LittleEndian(b[(p + 4)..]);
            NoopPolicy.Require(n <= b.Length - p - 8, "CHUNK_LENGTH " + name);
            int end = checked(p + 8 + (int)n), zeros = 0;
            // Not claimed to be padding: only observed zero suffix; exact byte diff covers padding too.
            for (int q = end - 1; q >= p + 8 && b[q] == 0; q--) zeros++;
            chunks.Add(new(name, p, (int)n, FrozenBytes.Hash(b.Slice(p + 8, (int)n)), end % 16, zeros)); p = end;
        }
        return new(b.Length - 8, chunks.ToImmutable());
    }
    public static BinaryDiff Compare(FrozenBytes left, FrozenBytes right, int limit)
    {
        NoopPolicy.Require(limit > 0 && limit <= 1024, "DIFF_LIMIT");
        var a = left.Span; var b = right.Span; long bytes = 0, ranges = 0; int p = 0;
        var entries = ImmutableArray.CreateBuilder<ByteRange>(); int max = Math.Max(a.Length, b.Length);
        while (p < max)
        {
            if (p < a.Length && p < b.Length && a[p] == b[p]) { p++; continue; }
            int start = p;
            do { bytes++; p++; } while (p < max && (p >= a.Length || p >= b.Length || a[p] != b[p]));
            ranges++;
            if (entries.Count < limit) entries.Add(new(start, p - start,
                start < a.Length ? Convert.ToHexString(a.Slice(start, Math.Min(32, Math.Min(p - start, a.Length - start)))) : "",
                start < b.Length ? Convert.ToHexString(b.Slice(start, Math.Min(32, Math.Min(p - start, b.Length - start)))) : ""));
        }
        return new(bytes, ranges, entries.ToImmutable(), ranges > entries.Count, a.Length == b.Length);
    }
}
