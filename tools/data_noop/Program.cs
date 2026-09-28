using System.Buffers.Binary;
using System.Collections;
using System.Collections.Immutable;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;
using CCDataNoop;
using UndertaleModLib;

internal static class Program
{
    static readonly JsonSerializerOptions Json = new() { WriteIndented = true };
    static string Game => NoopPolicy.GameRoot;
    static string? release;
    static readonly Dictionary<string, object?> Report = new()
    {
        ["schema"] = "cc-data-noop/v1", ["status"] = "blocked", ["serializerApproved"] = false,
        ["writeCalls"] = 0, ["dataOutput"] = null, ["sourceSha256Expected"] = NoopPolicy.SourceSha256,
        ["fontsMutatedByAdapter"] = false, ["installed"] = false, ["gameLaunched"] = false,
        ["snapshotComplete"] = false, ["semanticDiff"] = null, ["binaryDiff"] = null
    };
    [DllImport("kernel32.dll", EntryPoint = "CreateDirectoryW", ExactSpelling = true, CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)] static extern bool CreateDirectoryExclusive(string path, IntPtr security);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern uint QueryDosDevice(string name, StringBuilder target, int length);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern uint GetLongPathName(string path, StringBuilder expanded, int length);

    static void NoLinks(string path)
    {
        for (string? p = path; p != null; p = Path.GetDirectoryName(p))
            NoopPolicy.Require((File.GetAttributes(p) & FileAttributes.ReparsePoint) == 0, "REPARSE_POINT " + p);
    }
    static string Physical(string path)
    {
        NoLinks(path);
        var device = new StringBuilder(32768); var expanded = new StringBuilder(32768);
        NoopPolicy.Require(QueryDosDevice(path[..2], device, device.Capacity) > 0 &&
            System.Text.RegularExpressions.Regex.IsMatch(device.ToString(), @"\A\\Device\\HarddiskVolume[0-9]+\z"), "VOLUME_MAPPING " + path);
        uint n = GetLongPathName(path, expanded, expanded.Capacity);
        NoopPolicy.Require(n > 0 && n < expanded.Capacity, "LONG_PATH " + path);
        return device + expanded.ToString()[2..];
    }
    static void Reserve()
    {
        NoopPolicy.Require(OperatingSystem.IsWindows() && BitConverter.IsLittleEndian, "WINDOWS_LITTLE_ENDIAN_REQUIRED");
        string temp = Physical(NoopPolicy.TempRoot), game = Physical(Game);
        NoopPolicy.Require(!temp.StartsWith(game + "\\", StringComparison.OrdinalIgnoreCase) && !temp.Equals(game, StringComparison.OrdinalIgnoreCase), "OUTPUT_ALIASES_GAME");
        for (int i = 1; i <= 999999; i++)
        {
            string run = Path.Combine(NoopPolicy.TempRoot, "cc-data-noop-" + i.ToString("D3"));
            if (!CreateDirectoryExclusive(run, IntPtr.Zero))
            {
                int error = Marshal.GetLastWin32Error(); if (error == 183) continue;
                throw new GateRefusal("CREATE_RUN " + error);
            }
            string candidate = Path.Combine(run, "release"); NoopPolicy.OutputPath(candidate);
            NoLinks(run); NoopPolicy.Require(CreateDirectoryExclusive(candidate, IntPtr.Zero), "CREATE_RELEASE " + Marshal.GetLastWin32Error());
            release = candidate; NoLinks(release); Console.WriteLine("RELEASE " + release); return;
        }
        throw new GateRefusal("NO_FREE_RUN_DIRECTORY");
    }
    static void SaveJson(string name, object value)
    {
        NoopPolicy.Require(release != null, "NO_RELEASE"); NoLinks(release!);
        byte[] b = JsonSerializer.SerializeToUtf8Bytes(value, Json);
        string path = Path.Combine(release!, name);
        using (var s = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None)) { s.Write(b); s.Flush(true); }
        NoopPolicy.Require(FrozenBytes.ReadFile(path).Sha256 == FrozenBytes.Hash(b), "REPORT_READBACK " + name);
    }
    static object SaveSnapshot(string name, GraphSnapshot snapshot)
    {
        string path = Path.Combine(release!, name); NoLinks(release!);
        using (var file = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None))
        {
            using (var gzip = new GZipStream(file, CompressionLevel.Fastest, true))
            using (var writer = new StreamWriter(gzip, new UTF8Encoding(false, true)))
                foreach (var row in snapshot.Rows) writer.WriteLine(JsonSerializer.Serialize(row));
            file.Flush(true);
        }
        // Independent persisted readback, before the model writer is allowed to run.
        using (var file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
        using (var gzip = new GZipStream(file, CompressionMode.Decompress))
        using (var reader = new StreamReader(gzip, new UTF8Encoding(false, true)))
        {
            foreach (var expected in snapshot.Rows)
            {
                string? line = reader.ReadLine(); NoopPolicy.Require(line != null, "SNAPSHOT_TRUNCATED");
                NoopPolicy.Require(JsonSerializer.Deserialize<SnapshotRow>(line!) == expected, "SNAPSHOT_READBACK");
            }
            NoopPolicy.Require(reader.ReadLine() == null, "SNAPSHOT_TRAILING");
        }
        var frozen = FrozenBytes.ReadFile(path);
        return new { file = name, rows = snapshot.Rows.Length, snapshot.Sha256, compressedSha256 = frozen.Sha256, size = frozen.Length, snapshot.Types };
    }

    // All IList<T> aliases in the actual DLL must be known. No alias getter is invoked.
    static readonly Dictionary<string, (string chunk, string field)> Aliases = new()
    {
        ["Extensions"] = ("EXTN", "List"), ["Sounds"] = ("SOND", "List"), ["AudioGroups"] = ("AGRP", "List"),
        ["Sprites"] = ("SPRT", "List"), ["Backgrounds"] = ("BGND", "List"), ["Paths"] = ("PATH", "List"),
        ["Scripts"] = ("SCPT", "List"), ["GlobalInitScripts"] = ("GLOB", "List"), ["GameEndScripts"] = ("GMEN", "List"),
        ["Shaders"] = ("SHDR", "List"), ["Fonts"] = ("FONT", "List"), ["Timelines"] = ("TMLN", "List"),
        ["GameObjects"] = ("OBJT", "List"), ["Rooms"] = ("ROOM", "List"), ["TexturePageItems"] = ("TPAG", "List"),
        ["Code"] = ("CODE", "List"), ["Variables"] = ("VARI", "List"), ["Functions"] = ("FUNC", "Functions"),
        ["CodeLocals"] = ("FUNC", "CodeLocals"), ["Strings"] = ("STRG", "List"), ["EmbeddedImages"] = ("EMBI", "List"),
        ["EmbeddedTextures"] = ("TXTR", "List"), ["TextureGroupInfo"] = ("TGIN", "List"), ["EmbeddedAudio"] = ("AUDO", "List"),
        ["AnimationCurves"] = ("ACRV", "List"), ["Sequences"] = ("SEQN", "List"), ["FilterEffects"] = ("FEDS", "List"),
        ["ParticleSystems"] = ("PSYS", "List"), ["ParticleSystemEmitters"] = ("PSEM", "List")
    };
    static IEnumerable<FieldInfo> Fields(Type t)
    {
        for (Type? c = t; c != null; c = c.BaseType)
            foreach (var f in c.GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly)) yield return f;
    }
    static object? Field(object value, string name)
    {
        var f = Fields(value.GetType()).SingleOrDefault(f => f.Name == name);
        NoopPolicy.Require(f != null, "MISSING_FIELD " + value.GetType().FullName + "." + name); return f!.GetValue(value);
    }
    static IList LogicalList(object value)
    {
        Type t = value.GetType();
        if (t.IsGenericType && t.GetGenericTypeDefinition() == typeof(List<>)) return (IList)value;
        var collection = Fields(t).SingleOrDefault(f => f.DeclaringType!.IsGenericType &&
            f.DeclaringType.GetGenericTypeDefinition() == typeof(System.Collections.ObjectModel.Collection<>) && f.Name == "items");
        NoopPolicy.Require(collection != null, "UNKNOWN_LIST_STORAGE " + t.FullName);
        object? backing = collection!.GetValue(value);
        NoopPolicy.Require(backing != null && backing.GetType().IsGenericType && backing.GetType().GetGenericTypeDefinition() == typeof(List<>), "UNKNOWN_LIST_BACKING " + t.FullName);
        return (IList)backing!;
    }
    internal static (GraphSnapshot snapshot, object coverage) Capture(UndertaleData data, BinaryLayout raw)
    {
        NoopPolicy.Require(raw.Chunks.Select(c => c.Name).SequenceEqual(data.FORM.Chunks.Keys), "CHUNK_ORDER_MODEL");
        foreach (var c in raw.Chunks) NoopPolicy.Require(data.FORM.Chunks[c.Name].Length == c.Length, "CHUNK_LENGTH_MODEL " + c.Name);
        var roots = new List<GraphRoot> { new("Data", data) };
        foreach (var pair in data.FORM.Chunks) roots.Add(new("Chunk/" + pair.Key, pair.Value));
        var properties = typeof(UndertaleData).GetProperties().Where(p => p.PropertyType.IsGenericType &&
            p.PropertyType.GetGenericTypeDefinition() == typeof(IList<>)).OrderBy(p => p.Name, StringComparer.Ordinal).ToArray();
        var coverage = new List<object>();
        foreach (var p in properties)
        {
            NoopPolicy.Require(Aliases.TryGetValue(p.Name, out var alias), "UNKNOWN_RESOURCE_ALIAS " + p.Name);
            if (!data.FORM.Chunks.TryGetValue(alias.chunk, out var chunk))
            { roots.Add(new("Collection/" + p.Name, null)); coverage.Add(new { name = p.Name, chunk = alias.chunk, present = false, count = (int?)null }); continue; }
            object? container = Field(chunk, alias.field);
            roots.Add(new("Collection/" + p.Name, container));
            // YYC CODE may legitimately have a null list, distinct from an empty list.
            if (container == null) { coverage.Add(new { name = p.Name, chunk = alias.chunk, present = true, count = (int?)null }); continue; }
            NoopPolicy.Require(p.PropertyType.IsInstanceOfType(container), "RESOURCE_ALIAS_TYPE " + p.Name);
            var list = LogicalList(container);
            coverage.Add(new { name = p.Name, chunk = alias.chunk, present = true, count = (int?)list.Count });
            for (int i = 0; i < list.Count; i++) roots.Add(new("Resource/" + p.Name + "/" + i.ToString("D8"), list[i]));
        }
        var snapshot = GraphSnapshot.Capture(roots, new[] { typeof(UndertaleData).Assembly, typeof(Underanalyzer.Decompiler.DecompileSettings).Assembly });
        NoopPolicy.BeforeWrite(true, raw.Chunks.Length, data.FORM.Chunks.Count, coverage.Count == properties.Length);
        return (snapshot, new { chunks = raw.Chunks.Select(c => c.Name).ToArray(), resourceAliases = coverage, allInstanceFields = true, skippedFields = Array.Empty<string>() });
    }
    static long[] UidModel(UndertaleData data)
    {
        object info = Field(data.FORM.Chunks["GEN8"], "Object")!;
        var uid = (List<long>)Field(info, "<GMS2RandomUID>k__BackingField")!; return uid.ToArray();
    }
    static object UidBytes(FrozenBytes bytes, BinaryLayout layout)
    {
        var chunk = layout.Chunks.Single(c => c.Name == "GEN8"); int start = chunk.Offset + 8;
        var b = bytes.Span; NoopPolicy.Require(b[start + 1] >= 14 && BinaryPrimitives.ReadUInt32LittleEndian(b[(start + 44)..]) == 2, "UID_LAYOUT_VERSION");
        uint count = BinaryPrimitives.ReadUInt32LittleEndian(b[(start + 128)..]);
        long offset = start + 132L + 4L * count;
        NoopPolicy.Require(offset + 40 <= start + chunk.Length, "UID_LAYOUT_BOUNDS");
        int pos = checked((int)offset); var values = new long[5];
        for (int i = 0; i < 5; i++) values[i] = BinaryPrimitives.ReadInt64LittleEndian(b[(pos + 8 * i)..]);
        return new { field = "GEN8.GMS2RandomUID", absoluteOffset = pos, length = 40, roomCount = count,
            rawHex = Convert.ToHexString(b.Slice(pos, 40)), rawLittleEndianInt64 = values,
            note = "Raw five 8-byte slots; do not assume equality to UMT's reconstructed List<long>. Full GEN8 remains compared." };
    }
    static object AssemblyIdentity(Assembly a) => new { name = a.GetName().Name, version = a.GetName().Version?.ToString(),
        informationalVersion = a.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion,
        mvid = a.ManifestModule.ModuleVersionId, sha256 = FrozenBytes.ReadFile(a.Location).Sha256 };

    static int Main(string[] args)
    {
        int exit = 2;
        try
        {
            NoopPolicy.Arguments(args); Reserve();
            NoopPolicy.Require(FrozenBytes.ReadFile(typeof(UndertaleData).Assembly.Location).Sha256 == NoopPolicy.UmtSha256, "UMT_DLL_SHA256");
            NoopPolicy.Require(FrozenBytes.ReadFile(typeof(Underanalyzer.Decompiler.DecompileSettings).Assembly.Location).Sha256 == NoopPolicy.UnderanalyzerSha256, "UNDERANALYZER_DLL_SHA256");
            Report["runtime"] = RuntimeInformation.FrameworkDescription;
            Report["umt"] = AssemblyIdentity(typeof(UndertaleData).Assembly);
            Report["process"] = AssemblyIdentity(typeof(Program).Assembly);
            Report["cloneRevision"] = "f43e12c445c37d50dc6244caa12ccab232983f3f (not the DLL revision)";
            NoLinks(NoopPolicy.Source);
            var source = FrozenBytes.ReadFile(NoopPolicy.Source); source.RequireHash(NoopPolicy.SourceSha256);
            Report["sourceSize"] = source.Length; Report["sourceSha256"] = source.Sha256;
            var rawBefore = BinaryLayout.Parse(source); Report["binaryBefore"] = rawBefore;
            NoopPolicy.Require(rawBefore.Chunks.Length == 30, "SOURCE_CHUNK_COUNT");
            Console.WriteLine("SOURCE_VERIFIED " + source.Sha256 + " size=" + source.Length);
            using var input = source.OpenRead();
            using var data = UndertaleIO.Read(input, NoopPolicy.FatalWarning);
            Console.WriteLine("READ_OK; capturing every reachable field before any Write");
            // No externally loaded textures, and no lazy getter invocation.
            object textures = Field(data.FORM.Chunks["TXTR"], "List")!;
            foreach (object texture in LogicalList(textures))
                NoopPolicy.Require(Field(texture, "<TextureExternal>k__BackingField") is false &&
                    Field(texture, "<TextureLoaded>k__BackingField") is true, "EXTERNAL_OR_UNLOADED_TEXTURE");
            var (before, coverage) = Capture(data, rawBefore);
            Report["coverageBefore"] = coverage; Report["snapshotBefore"] = SaveSnapshot("before.snapshot.jsonl.gz", before);
            Report["snapshotComplete"] = true;
            Report["uidModelBeforeWrite"] = UidModel(data); Report["uidBytesBefore"] = UidBytes(source, rawBefore);
            SaveJson("prewrite.json", Report); // durable independent baseline, not a reference to the live model
            Console.WriteLine("SNAPSHOT_COMPLETE rows=" + before.Rows.Length + " sha256=" + before.Sha256);
            NoopPolicy.BeforeWrite(true, rawBefore.Chunks.Length, data.FORM.Chunks.Count, true);
            NoLinks(release!); string output = Path.Combine(release!, "noop.data.win");
            using (var stream = new FileStream(output, FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None))
            {
                Report["dataOutput"] = output; Report["writeCalls"] = 1;
                UndertaleIO.Write(stream, data); // THE ONLY GAME SERIALIZER CALL. No CLI and no font mutator.
                stream.Flush(true);
            }
            var result = FrozenBytes.ReadFile(output);
            Report["outputSha256"] = result.Sha256; Report["outputSize"] = result.Length;
            // Immediate readback of the flushed, re-opened output; every warning is fatal.
            using var outputInput = result.OpenRead();
            using var reread = UndertaleIO.Read(outputInput, NoopPolicy.FatalWarning);
            Report["readbackOk"] = true;
            Report["uidModelAfterWrite"] = UidModel(data); Report["uidModelReadback"] = UidModel(reread);
            var rawAfter = BinaryLayout.Parse(result); Report["binaryAfter"] = rawAfter;
            Report["uidBytesAfter"] = UidBytes(result, rawAfter);
            var binary = BinaryLayout.Compare(source, result, 64); Report["binaryDiff"] = binary;
            var (after, coverageAfter) = Capture(reread, rawAfter);
            Report["coverageAfter"] = coverageAfter; Report["snapshotAfter"] = SaveSnapshot("after.snapshot.jsonl.gz", after);
            var semantic = before.Diff(after, 64); Report["semanticDiff"] = semantic;
            bool identical = binary.ChangedBytes == 0;
            Report["binaryAssessment"] = new
            {
                formLengthEqual = rawBefore.FormLength == rawAfter.FormLength,
                chunkOrderEqual = rawBefore.Chunks.Select(c => c.Name).SequenceEqual(rawAfter.Chunks.Select(c => c.Name)),
                chunkLengthsEqual = rawBefore.Chunks.Select(c => c.Length).SequenceEqual(rawAfter.Chunks.Select(c => c.Length)),
                allPaddingBytesEqual = identical, allPointerBytesEqual = identical,
                method = "Exhaustive byte comparison, including all headers, payload, padding and absolute/relative pointer bytes. " +
                    "Zero suffixes are observations, NOT a claimed padding parser. If any byte differs, padding/pointer equivalence is unproven and approval is denied."
            };
            bool approved = NoopPolicy.CanAdvance(semantic.Total, binary.ChangedBytes);
            Report["serializerApproved"] = approved; Report["status"] = approved ? "no-op-verified" : "differences-blocked";
            Console.WriteLine($"ROUNDTRIP outputSha256={result.Sha256} size={result.Length} semantic={semantic.Total} bytes={binary.ChangedBytes} approved={approved}");
            exit = approved ? 0 : 2;
        }
        catch (Exception e)
        {
            Report["failure"] = e.ToString(); Report["status"] = "blocked"; Report["serializerApproved"] = false;
            Console.Error.WriteLine("BLOCKED " + e); exit = 2;
        }
        finally
        {
            if (release != null)
            {
                try
                {
                    NoLinks(NoopPolicy.Source);
                    string sourceAfter = FrozenBytes.ReadFile(NoopPolicy.Source).Sha256; Report["sourceSha256After"] = sourceAfter;
                    if (sourceAfter != NoopPolicy.SourceSha256) { Report["serializerApproved"] = false; Report["status"] = "source-integrity-failed"; exit = 2; }
                    if (Report["dataOutput"] is string path && File.Exists(path))
                    {
                        // Preserve and identify even a partial output after Write/readback failure. Never delete.
                        using var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
                        Report["retainedOutputSize"] = stream.Length;
                        Report["retainedOutputSha256"] = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(stream)).ToLowerInvariant();
                    }
                    Report["loadedAssemblies"] = AppDomain.CurrentDomain.GetAssemblies().Where(a => !a.IsDynamic && !string.IsNullOrEmpty(a.Location))
                        .OrderBy(a => a.GetName().Name, StringComparer.Ordinal).Select(AssemblyIdentity).ToArray();
                    SaveJson("report.json", Report); Console.WriteLine("REPORT " + Path.Combine(release, "report.json"));
                }
                catch (Exception e) { Console.Error.WriteLine("REPORT_OR_INTEGRITY_FAILED " + e); exit = 2; }
            }
        }
        return exit;
    }
}
