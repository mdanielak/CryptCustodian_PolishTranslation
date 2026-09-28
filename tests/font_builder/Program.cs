using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp.Scripting;
using UndertaleModLib;
using UndertaleModLib.Models;
using UndertaleModLib.Scripting;
using UndertaleModLib.Util;

// Dependency-free unit runner: no data.win (not even a synthetic one), no UMT IO,
// filesystem publication, atlas generation on disk, networking or game processes.
public static class Program
{
    static int passed, failed;
    static void Test(string name, Action test)
    {
        try { test(); passed++; Console.WriteLine("PASS " + name); }
        catch (Exception e) { failed++; Console.WriteLine("FAIL " + name + ": " + e); }
    }
    static void Equal<T>(T a, T b) { if (!Equals(a, b)) throw new Exception($"Expected {a}; got {b}"); }
    static void Refuses(Action f, string contains = null)
    {
        try { f(); }
        catch (Exception e) when (e is InvalidDataException || e is JsonException || e is DecoderFallbackException || e is InvalidOperationException)
        {
            if (contains != null && !e.Message.Contains(contains)) throw new Exception("Wrong refusal: " + e.Message);
            return;
        }
        throw new Exception("Expected refusal");
    }
    static byte[] Utf8(string s) => Encoding.UTF8.GetBytes(s);
    static JsonElement Element(JsonNode n) => FontBuilderCore.Parse(Utf8(n.ToJsonString()));
    static readonly byte[] Pixels = Enumerable.Repeat((byte)255, 18 * 4).ToArray();
    static Dictionary<string, byte[]> Atlases() => new Dictionary<string, byte[]> { ["Nerko.rgba"] = Pixels.ToArray() };
    static Dictionary<string, int[]> Existing() => new Dictionary<string, int[]> { ["Nerko"] = new[] { 65, (int)'ó', (int)'Ó' } };
    static JsonObject Fixture()
    {
        var glyphs = new JsonArray();
        int i = 0;
        foreach (char c in FontBuilderCore.Required.Where(c => c != 'ó' && c != 'Ó'))
            glyphs.Add(new JsonObject {
                ["character"] = c.ToString(), ["x"] = i++, ["y"] = 0, ["width"] = 1, ["height"] = 1,
                ["shift"] = 2, ["offset"] = -1,
                ["kerning"] = new JsonArray(new JsonObject { ["character"] = 65, ["shiftModifier"] = -2 })
            });
        return new JsonObject {
            ["schema"] = "cc-font-atlas/v1", ["required"] = FontBuilderCore.Required,
            ["fonts"] = new JsonArray(new JsonObject {
                ["name"] = "Nerko", ["atlas"] = new JsonObject {
                    ["file"] = "Nerko.rgba", ["format"] = "rgba8", ["width"] = 18, ["height"] = 1,
                    ["sha256"] = FontBuilderCore.Hash(Pixels)
                }, ["glyphs"] = glyphs
            })
        };
    }
    static JsonNode Font(JsonNode n) => n["fonts"][0];
    static JsonNode Glyph(JsonNode n, int i = 0) => Font(n)["glyphs"][i];
    static JsonElement Validate(JsonNode n, Dictionary<string, int[]> old = null, Dictionary<string, byte[]> images = null)
        => FontBuilderCore.ValidateManifest(Element(n), new[] { "Nerko" }, false, old ?? Existing(), images ?? Atlases());
    static void BadManifest(string name, Action<JsonObject> mutate, string reason = null)
        => Test(name, () => { var f = Fixture(); mutate(f); Refuses(() => Validate(f), reason); });

    public static int Main(string[] args)
    {
        Test("full 18 character contract", () => {
            Equal(18, FontBuilderCore.Required.Length); Equal(18, FontBuilderCore.Required.Distinct().Count());
        });
        Test("UTF-8 and escaped JSON round-trip", () => {
            var p = FontBuilderCore.Parse(JsonSerializer.SerializeToUtf8Bytes(new { text = FontBuilderCore.Required + "😀" }));
            Equal(FontBuilderCore.Required + "😀", p.GetProperty("text").GetString());
        });
        Test("strict UTF-8", () => Refuses(() => FontBuilderCore.Parse(new byte[] { 0xC0, 0xAF })));
        Test("duplicate JSON keys nested", () => Refuses(() => FontBuilderCore.Parse(Utf8("{\"a\":{\"b\":1,\"b\":2}}"))));
        Test("duplicate escaped JSON key", () => Refuses(() => FontBuilderCore.Parse(Utf8("{\"a\":1,\"\\u0061\":2}"))));
        Test("trailing JSON", () => Refuses(() => FontBuilderCore.Parse(Utf8("{} {}"))));
        Test("JSON comments", () => Refuses(() => FontBuilderCore.Parse(Utf8("{/*x*/}"))));
        Test("oversized JSON", () => Refuses(() => FontBuilderCore.Parse(new byte[1024 * 1024 + 1])));
        Test("hash known vector and uppercase", () => {
            Equal("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", FontBuilderCore.Hash(Utf8("abc")));
            FontBuilderCore.CheckHash(Pixels, FontBuilderCore.Hash(Pixels).ToUpperInvariant());
        });
        Test("missing expected hash", () => Refuses(() => FontBuilderCore.CheckHash(Pixels, null)));
        Test("incorrect expected hash", () => Refuses(() => FontBuilderCore.CheckHash(Pixels, new string('0', 64))));
        Test("invalid hash syntax", () => Refuses(() => FontBuilderCore.CheckHash(Pixels, new string('g', 64))));
        Test("valid additions and preservation, no mutation", () => {
            var m = Fixture(); var old = Existing(); var images = Atlases();
            string before = m.ToJsonString(), oldBefore = JsonSerializer.Serialize(old); var pixelsBefore = images["Nerko.rgba"].ToArray();
            var p = Validate(m, old, images)[0];
            Equal(16, p.GetProperty("additions").GetArrayLength()); Equal(3, p.GetProperty("preservedGlyphCount").GetInt32());
            Equal("óÓ", p.GetProperty("existingRequired").GetString());
            Equal(-1, p.GetProperty("additions")[0].GetProperty("offset").GetInt32());
            Equal(-2, p.GetProperty("additions")[0].GetProperty("kerning")[0].GetProperty("shiftModifier").GetInt32());
            Equal(before, m.ToJsonString()); Equal(oldBefore, JsonSerializer.Serialize(old)); Equal(true, pixelsBefore.SequenceEqual(images["Nerko.rgba"]));
        });
        Test("all 18 supplied for empty old font", () => {
            var f = Fixture(); var gs = Font(f)["glyphs"].AsArray();
            foreach (char c in "óÓ") { var g = Glyph(f).DeepClone(); g["character"] = c.ToString(); g["x"] = gs.Count; gs.Add(g); }
            Equal(18, Validate(f, new Dictionary<string, int[]> { ["Nerko"] = new[] { 65 } })[0].GetProperty("additions").GetArrayLength());
        });
        BadManifest("missing required letter", f => f["required"] = FontBuilderCore.Required.Substring(1));
        BadManifest("duplicate required letter", f => f["required"] = FontBuilderCore.Required.Substring(1) + "ć");
        BadManifest("missing addition", f => Font(f)["glyphs"].AsArray().RemoveAt(0), "Incomplete");
        BadManifest("duplicate addition", f => Font(f)["glyphs"].AsArray().Add(Glyph(f).DeepClone()), "Duplicate new");
        BadManifest("old/new codepoint collision", f => Glyph(f)["character"] = "ó", "Collision");
        BadManifest("unrelated codepoint", f => Glyph(f)["character"] = "A");
        BadManifest("decomposed Unicode refused", f => Glyph(f)["character"] = "a\u0328");
        BadManifest("astral glyph refused", f => Glyph(f)["character"] = "😀");
        BadManifest("overlapping rectangles", f => Glyph(f, 1)["x"] = 0, "Overlapping");
        BadManifest("out of bounds rectangle", f => Glyph(f)["x"] = 18, "outside");
        BadManifest("negative coordinate", f => Glyph(f)["x"] = -1);
        BadManifest("zero bitmap dimension", f => Glyph(f)["width"] = 0);
        BadManifest("unsigned width overflow", f => Glyph(f)["width"] = 65536);
        BadManifest("signed shift overflow", f => Glyph(f)["shift"] = 32768);
        BadManifest("signed offset underflow", f => Glyph(f)["offset"] = -32769);
        BadManifest("fractional metric", f => Glyph(f)["shift"] = 1.5);
        BadManifest("missing metric", f => Glyph(f).AsObject().Remove("shift"));
        BadManifest("unknown metric", f => Glyph(f)["advance"] = 2);
        BadManifest("kerning duplicate", f => Glyph(f)["kerning"].AsArray().Add(Glyph(f)["kerning"][0].DeepClone()));
        BadManifest("kerning absent predecessor", f => Glyph(f)["kerning"][0]["character"] = 66);
        BadManifest("kerning shift overflow", f => Glyph(f)["kerning"][0]["shiftModifier"] = 32768);
        Test("signed kerning character maps to ushort", () => {
            var f = Fixture(); Glyph(f)["kerning"][0]["character"] = -1;
            var old = Existing(); old["Nerko"] = old["Nerko"].Append(65535).ToArray(); Validate(f, old);
        });
        BadManifest("bitmap hash mismatch", f => Font(f)["atlas"]["sha256"] = new string('0', 64));
        BadManifest("unsupported PNG codec", f => Font(f)["atlas"]["format"] = "png");
        BadManifest("atlas dimension limit", f => Font(f)["atlas"]["width"] = 4097);
        Test("atlas length mismatch", () => Refuses(() => Validate(Fixture(), images: new Dictionary<string, byte[]> { ["Nerko.rgba"] = new byte[1] })));
        Test("atlas absent", () => Refuses(() => Validate(Fixture(), images: new Dictionary<string, byte[]>())));
        Test("unused atlas", () => { var a = Atlases(); a.Add("extra.rgba", Pixels); Refuses(() => Validate(Fixture(), images: a)); });
        Test("transparent bitmap", () => {
            var f = Fixture(); var pixels = new byte[72]; Font(f)["atlas"]["sha256"] = FontBuilderCore.Hash(pixels);
            Refuses(() => Validate(f, images: new Dictionary<string, byte[]> { ["Nerko.rgba"] = pixels }), "transparent");
        });
        Test("missing base font", () => Refuses(() => Validate(Fixture(), new Dictionary<string, int[]>())));
        Test("duplicate base glyph", () => Refuses(() => Validate(Fixture(), new Dictionary<string, int[]> { ["Nerko"] = new[] { 65, 65 } })));
        Test("base surrogate", () => Refuses(() => Validate(Fixture(), new Dictionary<string, int[]> { ["Nerko"] = new[] { 0xD800 } })));
        BadManifest("duplicate manifest font", f => f["fonts"].AsArray().Add(Font(f).DeepClone()));
        BadManifest("unselected manifest font", f => Font(f)["name"] = "NerkoSmall");
        Test("selection allowlist", () => FontBuilderCore.ValidateSelection(new[] { "Nerko", "NerkoLarge", "NerkoLarge2", "NerkoSmall" }, false));
        Test("Font_ES explicit opt in", () => {
            Refuses(() => FontBuilderCore.ValidateSelection(new[] { "Font_ES" }, false));
            FontBuilderCore.ValidateSelection(new[] { "Font_ES" }, true);
        });
        Test("Font_ES full 17-addition manifest", () => {
            var f = Fixture(); Font(f)["name"] = "Font_ES";
            var g = Glyph(f).DeepClone(); g["character"] = "Ó"; g["x"] = 16; Font(f)["glyphs"].AsArray().Add(g);
            var old = new Dictionary<string, int[]> { ["Font_ES"] = new[] { 65, (int)'ó' } };
            var plan = FontBuilderCore.ValidateManifest(Element(f), new[] { "Font_ES" }, true, old, Atlases());
            Equal(17, plan[0].GetProperty("additions").GetArrayLength());
        });
        Test("atlas reused by two selected fonts refused", () => {
            var f = Fixture(); var other = Font(f).DeepClone(); other["name"] = "NerkoSmall"; f["fonts"].AsArray().Add(other);
            var old = Existing(); old.Add("NerkoSmall", old["Nerko"].ToArray());
            Refuses(() => FontBuilderCore.ValidateManifest(Element(f), new[] { "Nerko", "NerkoSmall" }, false, old, Atlases()), "Duplicate atlas");
        });
        Test("empty selection", () => Refuses(() => FontBuilderCore.ValidateSelection(new string[0], false)));
        Test("duplicate selection", () => Refuses(() => FontBuilderCore.ValidateSelection(new[] { "Nerko", "Nerko" }, false)));
        Test("case-sensitive allowlist", () => Refuses(() => FontBuilderCore.ValidateSelection(new[] { "nerko" }, false)));
        Test("other font refused", () => Refuses(() => FontBuilderCore.ValidateSelection(new[] { "Font_CN" }, true)));
        foreach (string path in new[] { "..\\x", "C:x", @"\\server\share\x", @"\\?\C:\x", @"C:\x\..\release", @"C:\x\a:ads", @"C:\x\NUL.json", @"C:\x\tail.\x", @"C:\x\tail \x", @"C:\x\a/b", @"C:\x\\b" })
            Test("unsafe absolute path " + path, () => Refuses(() => FontBuilderCore.Absolute(path)));
        foreach (string leaf in new[] { "../x", "a/b", "a\\b", "NUL.rgba", "a:stream", "a.", "..", "a..b" })
            Test("unsafe atlas leaf " + leaf, () => Refuses(() => FontBuilderCore.Leaf(leaf)));
        Test("external release allowed", () => FontBuilderCore.OutputPolicy(@"D:\build\release", @"D:\build\release\audit.json", @"F:\game"));
        Test("game release denied case insensitive", () => Refuses(() => FontBuilderCore.OutputPolicy(@"F:\GAME\work\release", @"F:\GAME\work\release\audit.json", @"f:\game")));
        Test("prefix sibling is not child", () => FontBuilderCore.OutputPolicy(@"F:\game-other\release", @"F:\game-other\release\audit.json", @"F:\game"));
        Test("release escape", () => Refuses(() => FontBuilderCore.OutputPolicy(@"D:\build\release", @"D:\build\audit.json", @"F:\game")));
        Test("data output refused", () => Refuses(() => FontBuilderCore.OutputPolicy(@"D:\build\release", @"D:\build\release\data.win", @"F:\game")));
        Test("release name required", () => Refuses(() => FontBuilderCore.OutputPolicy(@"D:\build\out", @"D:\build\out\audit.json", @"F:\game")));
        Test("build gate cannot succeed", () => Refuses(FontBuilderCore.BuildGate, "BUILD_DISABLED"));
        Test("strict host invocation", () => FontBuilderCore.Invocation(new[] { "cli", "load", @"F:\game\data.win", "--scripts", @"D:\tools\builder.csx" }, @"D:\tools\builder.csx"));
        foreach (string flag in new[] { "-o", "--output", "-l", "--line", "-i", "--interactive", "-f", "--overwrite", "--output=x" })
            Test("host extra option refused " + flag, () => Refuses(() => FontBuilderCore.Invocation(new[] { "cli", "load", @"F:\game\data.win", "--scripts", @"D:\tools\builder.csx", flag }, @"D:\tools\builder.csx")));
        Test("host multiple scripts refused", () => Refuses(() => FontBuilderCore.Invocation(new[] { "cli", "load", @"F:\game\data.win", "--scripts", @"D:\tools\builder.csx", @"D:\tools\other.csx" }, @"D:\tools\builder.csx")));
        Test("host wrong script refused", () => Refuses(() => FontBuilderCore.Invocation(new[] { "cli", "load", @"F:\game\data.win", "-s", @"D:\tools\other.csx" }, @"D:\tools\builder.csx")));
        Test("request validates required hash and strict fields", () => {
            var r = new JsonObject { ["schema"] = "cc-font-request/v1", ["mode"] = "preflight", ["source"] = @"F:\game\data.win",
                ["expectedSha256"] = new string('a', 64), ["manifest"] = @"D:\inputs\manifest.json", ["releaseRoot"] = @"D:\build\release",
                ["output"] = @"D:\build\release\audit.json", ["fonts"] = new JsonArray("Nerko"), ["allowFontES"] = false };
            FontBuilderCore.Request(Utf8(r.ToJsonString())); r["mode"] = "build"; FontBuilderCore.Request(Utf8(r.ToJsonString()));
            r.Remove("expectedSha256"); Refuses(() => FontBuilderCore.Request(Utf8(r.ToJsonString())));
        });
        Test("installed DLL API contract (no game model loaded)", () => {
            Equal(typeof(ushort), typeof(UndertaleFont.Glyph).GetProperty("Character").PropertyType);
            Equal(typeof(short), typeof(UndertaleFont.Glyph).GetProperty("Shift").PropertyType);
            Equal(typeof(short), typeof(UndertaleFont.Glyph.GlyphKerning).GetProperty("Character").PropertyType);
            Equal(typeof(UndertaleTexturePageItem), typeof(UndertaleFont).GetProperty("Texture").PropertyType);
            Equal("0.9.2.0", typeof(UndertaleModCli.Program).Assembly.GetName().Version.ToString());
            Equal(FontBuilderCore.LocalLibraryInfo, typeof(UndertaleData).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion);
            Console.WriteLine("DLL " + typeof(UndertaleData).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion);
            Console.WriteLine("CLI " + typeof(UndertaleModCli.Program).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion);
        });
        Test("actual CSX compiles with local CLI script references; NOT executed", () => {
            if (args.Length != 1) throw new Exception("Pass absolute path to umt_font_builder.csx");
            string script = Path.GetFullPath(args[0]);
            var options = ScriptingUtil.CreateDefaultScriptOptions()
                .AddReferences(typeof(UndertaleModCli.Program).Assembly, typeof(Newtonsoft.Json.JsonConvert).Assembly)
                .WithFilePath(script).WithFileEncoding(Encoding.UTF8);
            var diagnostics = CSharpScript.Create(File.ReadAllText(script), options, typeof(IScriptInterface)).Compile();
            var errors = diagnostics.Where(d => d.Severity == DiagnosticSeverity.Error).ToArray();
            if (errors.Length != 0) throw new Exception(string.Join(Environment.NewLine, errors.Select(e => e.ToString())));
        });
        Console.WriteLine($"RESULT: {passed} passed, {failed} failed; data.win IO=0; CSX executions=0");
        return failed == 0 ? 0 : 1;
    }
}
