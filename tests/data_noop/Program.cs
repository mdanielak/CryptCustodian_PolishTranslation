using System.Collections.Immutable;
using System.Text;
using CCDataNoop;

internal static class Program
{
    sealed class Node
    {
        public string Text = "Zażółć gęślą jaźń — ąćęłńóśźżĄĆĘŁŃÓŚŹŻ";
        public Node? Next;
        public List<Node> Items = new();
        public Dictionary<string, Node> Map = new();
        public byte[] Payload = { 0, 1, 255 };
        public float Float = -0f;
        public long[] GMS2RandomUID = { -1, long.MaxValue };
        public int Getter => throw new Exception("GETTER MUST NEVER RUN");
    }
    sealed class Unsupported { public Stream Handle = Stream.Null; }
    class Base { private int hidden = 1; public void Change() => hidden++; }
    sealed class Derived : Base { public int Other = 4; }
    sealed class Native { public IntPtr Pointer = new IntPtr(1); }
    sealed class Callback
    {
        public Action? Action;
        public int State = 1;
        public void NeverInvoke() => throw new Exception("CALLBACK INVOKED");
    }
    sealed class CustomComparer : IEqualityComparer<string>
    {
        public int State;
        public bool Equals(string? a, string? b) => a == b;
        public int GetHashCode(string value) => value.GetHashCode();
    }
    static int count;
    static void Check(bool ok, string name)
    { if (!ok) throw new Exception(name); count++; Console.WriteLine("OK " + name); }
    static void Refuse(Action a, string text)
    {
        try { a(); } catch (GateRefusal e) { Check(e.Message.Contains(text), "refusal " + text); return; }
        throw new Exception("Missing refusal: " + text);
    }
    static GraphSnapshot Snap(object x, int limit = 10000) => GraphSnapshot.Capture(
        new[] { new GraphRoot("root", x) }, new[] { typeof(Program).Assembly }, limit);
    static Node Fixture()
    {
        var a = new Node(); var b = new Node { Text = "drugi", Next = a };
        a.Next = a; a.Items.Add(a); a.Items.Add(b); a.Map.Add("ą", b); a.Map.Add("ó", a);
        return a;
    }
    static byte[] Form(params (string name, byte[] payload)[] chunks)
    {
        using var s = new MemoryStream(); using var w = new BinaryWriter(s);
        w.Write(Encoding.ASCII.GetBytes("FORM")); w.Write(0);
        foreach (var c in chunks) { w.Write(Encoding.ASCII.GetBytes(c.name)); w.Write(c.payload.Length); w.Write(c.payload); }
        w.Flush(); s.Position = 4; w.Write((int)s.Length - 8); w.Flush(); return s.ToArray();
    }
    static int Main()
    {
        var a = Fixture(); var before = Snap(a); var independent = Snap(Fixture());
        Check(before.Diff(independent, 8).Total == 0, "independent graph, cycles, aliasing, Polish UTF-16 exact");
        Check(before.Sha256 == independent.Sha256, "deterministic snapshot hash");
        a.Text = "zmieniony"; a.Payload[0] = 7; a.GMS2RandomUID[0] = 19;
        var delta = before.Diff(Snap(a), 1);
        Check(delta.Total >= 3 && delta.Items.Length == 1 && delta.Truncated, "bounded diff counts ALL differences");
        Check(before.Diff(independent, 8).Total == 0, "snapshot is detached before writer mutation");
        Check(before.Rows.Any(r => r.Value.Contains("sha256:")), "payload hashes");
        a = Fixture(); a.Items.Reverse(); Check(before.Diff(Snap(a), 8).Total > 0, "resource order");
        a = Fixture(); a.Next = a.Items[1]; Check(before.Diff(Snap(a), 8).Total > 0, "reference target");
        a = Fixture(); a.Map.Clear(); a.Map.Add("ó", a); a.Map.Add("ą", a.Items[1]);
        Check(before.Diff(Snap(a), 8).Total > 0, "dictionary order");
        a = Fixture(); a.Next = Fixture(); Check(before.Diff(Snap(a), 8).Total > 0, "equal values do not collapse identity");
        a = Fixture(); a.Float = 0f; Check(before.Diff(Snap(a), 8).Total > 0, "float signed zero bits");
        a = Fixture(); a.Float = BitConverter.Int32BitsToSingle(unchecked((int)0x7fc00001));
        var nan = Snap(a); a.Float = BitConverter.Int32BitsToSingle(unchecked((int)0x7fc00002));
        Check(nan.Diff(Snap(a), 8).Total > 0, "NaN payload bits");
        a = Fixture(); a.Text = "\ud800"; Check(Snap(a).Sha256 != before.Sha256, "unpaired UTF-16 preserved without fallback encoding");
        var cube = new int[2, 3]; cube[1, 2] = 9;
        Check(Snap(cube).Diff(Snap(new int[3, 2]), 4).Total > 0, "array shape");
        Refuse(() => Snap(new Unsupported()), "UNSUPPORTED_TYPE");
        Refuse(() => Snap(new Unsupported()), "root/root/field/Program+Unsupported/Handle");
        Refuse(() => Snap(new Native()), "NATIVE_POINTER");
        var derived = new Derived(); var oldDerived = Snap(derived); derived.Change();
        Check(oldDerived.Diff(Snap(derived), 8).Total == 1, "private inherited field");
        var callback = new Callback(); callback.Action = callback.NeverInvoke;
        var oldCallback = Snap(callback); callback.State++;
        Check(oldCallback.Diff(Snap(callback), 8).Total == 1, "callback metadata and target; no execution");
        var comparer = new CustomComparer(); var dictionary = new Dictionary<string, Node>(comparer) { ["a"] = Fixture() };
        var oldDictionary = Snap(dictionary); comparer.State++;
        Check(oldDictionary.Diff(Snap(dictionary), 8).Total == 1, "custom comparer state is covered");
        Refuse(() => Snap(Fixture(), 2), "SNAPSHOT_LIMIT");
        Refuse(() => GraphSnapshot.Capture(new[] { new GraphRoot("same", a), new GraphRoot("same", a) }, new[] { typeof(Program).Assembly }), "DUPLICATE_ROOT");
        Refuse(() => before.Diff(independent, 0), "DIFF_LIMIT");
        var input = new byte[] { 1, 2, 3 }; var frozen = FrozenBytes.CopyOf(input); input[0] = 9;
        Check(frozen.Span[0] == 1, "immutable input owns its copy");
        using (var s = frozen.OpenRead()) { Check(!s.CanWrite, "input stream is read only"); }
        Refuse(() => frozen.RequireHash(new string('0', 64)), "SOURCE_SHA256");
        frozen.RequireHash(frozen.Sha256); Check(true, "exact source hash accepted");
        Refuse(() => NoopPolicy.Arguments(new[] { "--round-trip", "-o", "x" }), "ARGUMENTS");
        Refuse(() => NoopPolicy.Arguments(Array.Empty<string>()), "ARGUMENTS");
        NoopPolicy.Arguments(new[] { "--round-trip" }); Check(true, "only explicit no-op invocation");
        string? oldGame = Environment.GetEnvironmentVariable("CC_GAME_ROOT");
        string? oldOutput = Environment.GetEnvironmentVariable("CC_OUTPUT_ROOT");
        try
        {
            Environment.SetEnvironmentVariable("CC_GAME_ROOT", null);
            Refuse(() => { _ = NoopPolicy.Source; }, "CONFIG_REQUIRED");
            Environment.SetEnvironmentVariable("CC_GAME_ROOT", @"F:\game");
            Check(NoopPolicy.Source == @"F:\game\data.win", "source derived from explicit game root");
            Environment.SetEnvironmentVariable("CC_OUTPUT_ROOT", @"D:\build");
            Check(NoopPolicy.TempRoot == @"D:\build", "explicit output root");
            NoopPolicy.OutputPath(@"D:\build\cc-data-noop-999\release");
            foreach (string invalid in new[] { "relative", @"D:\build\..\game", @"\\server\share", @"D:\build:ads", @"D:\build\tail.", @"D:\NUL.json", "D:\\bad\npath" })
            {
                Environment.SetEnvironmentVariable("CC_OUTPUT_ROOT", invalid);
                Refuse(() => { _ = NoopPolicy.TempRoot; }, "CONFIG_PATH");
            }
            Environment.SetEnvironmentVariable("CC_OUTPUT_ROOT", null);
            Check(NoopPolicy.TempRoot == Path.TrimEndingDirectorySeparator(Path.GetTempPath()), "system temp default");
        }
        finally
        {
            Environment.SetEnvironmentVariable("CC_GAME_ROOT", oldGame);
            Environment.SetEnvironmentVariable("CC_OUTPUT_ROOT", oldOutput);
        }
        Refuse(() => NoopPolicy.OutputPath(@"F:\game\release"), "OUTPUT_PATH");
        Refuse(() => NoopPolicy.OutputPath(NoopPolicy.TempRoot + @"\cc-data-noop-001\release\..\data.win"), "OUTPUT_PATH");
        Refuse(() => NoopPolicy.OutputPath(NoopPolicy.TempRoot + @"\cc-data-noop-001\release:data"), "OUTPUT_PATH");
        Refuse(() => NoopPolicy.OutputPath(@"\\server\share\release"), "OUTPUT_PATH");
        NoopPolicy.OutputPath(NoopPolicy.TempRoot + @"\cc-data-noop-001\release"); Check(true, "fixed external release policy");
        Refuse(() => NoopPolicy.BeforeWrite(false, 30, 30, true), "SNAPSHOT_INCOMPLETE");
        Refuse(() => NoopPolicy.BeforeWrite(true, 29, 30, true), "CHUNK_COVERAGE");
        Refuse(() => NoopPolicy.BeforeWrite(true, 30, 29, true), "CHUNK_COVERAGE");
        Refuse(() => NoopPolicy.BeforeWrite(true, 30, 30, false), "COLLECTION_COVERAGE");
        Refuse(() => NoopPolicy.FatalWarning("synthetic", false), "UMT_WARNING");
        Refuse(() => NoopPolicy.FatalWarning("synthetic", true), "UMT_WARNING");
        var binary = Form(("GEN8", new byte[16]), ("FONT", new byte[] { 1, 2, 0, 0 }));
        var layout = BinaryLayout.Parse(FrozenBytes.CopyOf(binary));
        Check(layout.Chunks.Length == 2 && layout.Chunks[1].Offset == 32, "FORM lengths and order");
        Check(BinaryLayout.Compare(FrozenBytes.CopyOf(binary), FrozenBytes.CopyOf(binary), 4).ChangedBytes == 0, "binary identical");
        var changed = (byte[])binary.Clone(); changed[17] = 1; changed[19] = 2;
        var bd = BinaryLayout.Compare(FrozenBytes.CopyOf(binary), FrozenBytes.CopyOf(changed), 1);
        Check(bd.ChangedBytes == 2 && bd.RangeCount == 2 && bd.Ranges.Length == 1, "binary diff bounded without lost counts");
        Refuse(() => BinaryLayout.Parse(FrozenBytes.CopyOf(binary[..^1])), "FORM_LENGTH");
        changed = (byte[])binary.Clone(); changed[12] = 255;
        Refuse(() => BinaryLayout.Parse(FrozenBytes.CopyOf(changed)), "CHUNK_LENGTH");
        Refuse(() => BinaryLayout.Parse(FrozenBytes.CopyOf(Form(("FONT", new byte[0]), ("FONT", new byte[0])))), "DUPLICATE_CHUNK");
        Refuse(() => BinaryLayout.Parse(FrozenBytes.CopyOf(Form(("bad!", new byte[0])))), "CHUNK_NAME");
        Check(!NoopPolicy.CanAdvance(1, 0) && !NoopPolicy.CanAdvance(0, 1) && NoopPolicy.CanAdvance(0, 0), "no ignored GEN8 or other differences");
        var umt = typeof(UndertaleModLib.UndertaleData).Assembly;
        Check(FrozenBytes.ReadFile(umt.Location).Sha256 == NoopPolicy.UmtSha256, "actual pinned UMT DLL");
        var model = new UndertaleModLib.UndertaleData { FORM = new UndertaleModLib.UndertaleChunkFORM() };
        var underanalyzer = typeof(Underanalyzer.Decompiler.DecompileSettings).Assembly;
        Check(FrozenBytes.ReadFile(underanalyzer.Location).Sha256 == NoopPolicy.UnderanalyzerSha256, "actual pinned Underanalyzer DLL");
        var gen8 = new UndertaleModLib.UndertaleChunkGEN8 { Object = new UndertaleModLib.Models.UndertaleGeneralInfo() };
        model.FORM.Chunks.Add("GEN8", gen8); model.FORM.ChunksTypeDict.Add(gen8.GetType(), gen8);
        var umtBefore = GraphSnapshot.Capture(new[] { new GraphRoot("synthetic-UMT", model) }, new[] { umt, underanalyzer });
        gen8.Object.GMS2RandomUID.Add(17);
        var umtAfter = GraphSnapshot.Capture(new[] { new GraphRoot("synthetic-UMT", model) }, new[] { umt, underanalyzer });
        Check(umtBefore.Diff(umtAfter, 8).Total > 0, "real UMT graph: UID list mutation cannot rewrite baseline");
        Check(umtBefore.Rows.Any(r => r.Path.Contains("GMS2RandomUID")), "actual private UID backing field included");
        Check(umt.GetType("UndertaleModLib.UndertaleIO")!.GetMethods().Any(m => m.Name == "Write" && m.GetParameters()[0].ParameterType == typeof(Stream)), "actual direct stream Write API available; not invoked");
        Console.WriteLine($"PASS {count}; no game read/write, no UMT serializer execution"); return 0;
    }
}
