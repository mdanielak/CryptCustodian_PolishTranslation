using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

// Shared verbatim by CSX and the offline unit tests. No UMT, filesystem or game access.
public static class FontBuilderCore
{
    public const string Required = "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ";
    public const string LocalLibraryInfo = "1.0.0+03c7048438347799e0717b6e57a9358d000472e0";
    public const string Blocker = "BUILD_DISABLED: no verified isolated FONT/TPAG/TXTR/TGIN writer and data round-trip validator";
    public static void Require(bool ok, string reason)
    {
        if (!ok) throw new InvalidDataException(reason);
    }

    public static string Hash(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    public static void CheckHash(byte[] bytes, string expected)
    {
        Require(expected != null && Regex.IsMatch(expected, "\\A[0-9a-fA-F]{64}\\z"), "Expected SHA-256 required");
        Require(Hash(bytes).Equals(expected, StringComparison.OrdinalIgnoreCase), "SHA-256 mismatch");
    }

    public static JsonElement Parse(byte[] bytes)
    {
        Require(bytes.Length <= 1024 * 1024, "JSON exceeds 1 MiB");
        var utf8 = new UTF8Encoding(false, true);
        string text = utf8.GetString(bytes);
        Require(utf8.GetBytes(text).SequenceEqual(bytes), "UTF-8 round-trip failed");
        using (var doc = JsonDocument.Parse(text, new JsonDocumentOptions { MaxDepth = 32 }))
        {
            CheckDuplicates(doc.RootElement);
            return doc.RootElement.Clone();
        }
    }

    static void CheckDuplicates(JsonElement e)
    {
        if (e.ValueKind == JsonValueKind.Object)
        {
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (var p in e.EnumerateObject())
            {
                Require(seen.Add(p.Name), "Duplicate JSON property: " + p.Name);
                CheckDuplicates(p.Value);
            }
        }
        else if (e.ValueKind == JsonValueKind.Array)
            foreach (var item in e.EnumerateArray()) CheckDuplicates(item);
    }

    public static void Fields(JsonElement e, params string[] fields)
    {
        Require(e.ValueKind == JsonValueKind.Object, "Object required");
        var actual = e.EnumerateObject().Select(p => p.Name).ToArray();
        Require(actual.Length == fields.Length && new HashSet<string>(actual).SetEquals(fields),
                "Missing/unknown object fields; expected " + string.Join(",", fields));
    }

    public static string Text(JsonElement e, string key)
    {
        Require(e.GetProperty(key).ValueKind == JsonValueKind.String, "String required: " + key);
        return e.GetProperty(key).GetString();
    }
    public static int Number(JsonElement e, string key, int min, int max)
    {
        var v = e.GetProperty(key);
        int n;
        Require(v.ValueKind == JsonValueKind.Number && v.TryGetInt32(out n), "Integer required: " + key);
        n = v.GetInt32();
        Require(n >= min && n <= max, "Out of range: " + key);
        return n;
    }
    public static JsonElement[] Array(JsonElement e, string key, int min, int max)
    {
        var a = e.GetProperty(key);
        Require(a.ValueKind == JsonValueKind.Array, "Array required: " + key);
        Require(a.GetArrayLength() >= min && a.GetArrayLength() <= max, "Array length: " + key);
        return a.EnumerateArray().ToArray();
    }
    public static string[] Selection(JsonElement request)
    {
        var names = Array(request, "fonts", 1, 5).Select(v => {
            Require(v.ValueKind == JsonValueKind.String, "Font name must be string");
            return v.GetString();
        }).ToArray();
        var flag = request.GetProperty("allowFontES");
        Require(flag.ValueKind == JsonValueKind.True || flag.ValueKind == JsonValueKind.False, "Boolean allowFontES required");
        ValidateSelection(names, flag.GetBoolean());
        return names;
    }
    public static void ValidateSelection(string[] names, bool allowES)
    {
        var allowed = new HashSet<string>(new[] { "Nerko", "NerkoLarge", "NerkoLarge2", "NerkoSmall" }, StringComparer.Ordinal);
        if (allowES) allowed.Add("Font_ES");
        Require(names.Length > 0 && names.Length <= 5 && names.Distinct(StringComparer.Ordinal).Count() == names.Length,
                "Empty/duplicate font selection");
        Require(names.All(allowed.Contains), "Font outside explicit allowlist");
    }

    public static JsonElement Request(byte[] bytes)
    {
        var r = Parse(bytes);
        Fields(r, "schema", "mode", "source", "expectedSha256", "manifest", "releaseRoot", "output", "fonts", "allowFontES");
        Require(Text(r, "schema") == "cc-font-request/v1", "Request schema");
        Require(new[] { "preflight", "build" }.Contains(Text(r, "mode")), "Unknown mode");
        Require(Regex.IsMatch(Text(r, "expectedSha256"), "\\A[0-9a-fA-F]{64}\\z"), "Expected SHA-256 required");
        foreach (string k in new[] { "source", "manifest", "releaseRoot", "output" }) Absolute(Text(r, k));
        Selection(r);
        return r;
    }

    public static string Leaf(string value)
    {
        Require(!string.IsNullOrEmpty(value) && Regex.IsMatch(value, "\\A[A-Za-z0-9_][A-Za-z0-9_.-]*\\z") &&
                !value.EndsWith(".") && !value.Contains(".."), "Unsafe leaf name");
        Require(!Regex.IsMatch(value.Split('.')[0], "\\A(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])\\z", RegexOptions.IgnoreCase), "Device name");
        return value;
    }
    public static string Absolute(string path)
    {
        Require(path != null && Regex.IsMatch(path, "\\A[A-Za-z]:\\\\"), "Absolute local Windows path required");
        var parts = path.Substring(3).Split('\\');
        Require(parts.Length > 0 && parts.All(p => p.Length > 0 && p != "." && p != ".." &&
            !p.EndsWith(".") && !p.EndsWith(" ") && !p.Any(c => c < 32 || "<>:\"/|?*".Contains(c))), "Unsafe path component");
        foreach (string p in parts)
            Require(!Regex.IsMatch(p.Split('.')[0], "\\A(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])\\z", RegexOptions.IgnoreCase), "Device path");
        return path;
    }
    public static bool Within(string path, string root) => path.Equals(root, StringComparison.OrdinalIgnoreCase) ||
        path.StartsWith(root + "\\", StringComparison.OrdinalIgnoreCase);

    public static void OutputPolicy(string release, string output, params string[] protectedRoots)
    {
        Absolute(release); Absolute(output);
        Require(Path.GetFileName(release).Equals("release", StringComparison.OrdinalIgnoreCase), "Directory must be named release");
        Require(Path.GetDirectoryName(output).Equals(release, StringComparison.OrdinalIgnoreCase), "Output must be a direct child of release");
        Leaf(Path.GetFileName(output));
        Require(Path.GetExtension(output).Equals(".json", StringComparison.OrdinalIgnoreCase), "Only a JSON preflight report is supported");
        foreach (var root in protectedRoots)
        {
            Absolute(root);
            Require(!Within(release, root), "Release inside protected game/source installation");
        }
    }

    public static void Invocation(string[] argv, string scriptPath)
    {
        Require(argv.Length == 5 && argv[1] == "load" && (argv[3] == "--scripts" || argv[3] == "-s"),
            "Use only: UndertaleModCli load <source> --scripts <this-script>; no -o/-l/-i or other scripts");
        Absolute(argv[2]); Absolute(argv[4]); Absolute(scriptPath);
        Require(argv[4].Equals(scriptPath, StringComparison.OrdinalIgnoreCase), "Unexpected script path");
    }

    public static JsonElement[] ManifestFonts(JsonElement m)
    {
        Fields(m, "schema", "required", "fonts");
        Require(Text(m, "schema") == "cc-font-atlas/v1", "Manifest schema");
        string required = Text(m, "required");
        Require(required.Length == 18 && new HashSet<char>(required).SetEquals(Required), "Exactly 18 required Polish characters expected");
        return Array(m, "fonts", 1, 5);
    }

    // Atlas coordinates are input bitmap coordinates, NOT proposed TPAG coordinates.
    public static JsonElement ValidateManifest(JsonElement m, string[] selected, bool allowES,
        Dictionary<string, int[]> existing, Dictionary<string, byte[]> atlases)
    {
        ValidateSelection(selected, allowES);
        var fonts = ManifestFonts(m);
        var names = fonts.Select(f => Text(f, "name")).ToArray();
        Require(names.Distinct(StringComparer.Ordinal).Count() == names.Length && new HashSet<string>(names).SetEquals(selected),
                "Manifest selection mismatch/duplicate fonts");
        var usedAtlases = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var plans = new List<object>();
        foreach (var f in fonts)
        {
            Fields(f, "name", "atlas", "glyphs");
            string name = Text(f, "name");
            Require(existing.ContainsKey(name), "Missing base font: " + name);
            var old = existing[name];
            Require(old.Length <= 65536 && old.All(c => c >= 0 && c <= 65535 && (c < 0xD800 || c > 0xDFFF)) &&
                    old.Distinct().Count() == old.Length, "Invalid/duplicate old glyphs");
            var a = f.GetProperty("atlas");
            Fields(a, "file", "format", "width", "height", "sha256");
            string file = Leaf(Text(a, "file"));
            Require(usedAtlases.Add(file), "Duplicate atlas file (one atlas per font)");
            Require(Text(a, "format") == "rgba8", "Only raw rgba8 supported");
            int width = Number(a, "width", 1, 4096), height = Number(a, "height", 1, 4096);
            Require(atlases.ContainsKey(file), "Missing bitmap atlas");
            byte[] pixels = atlases[file];
            Require(pixels.Length == checked(width * height * 4), "Atlas byte length mismatch");
            CheckHash(pixels, Text(a, "sha256"));
            var glyphs = Array(f, "glyphs", 0, 18);
            var codes = new HashSet<int>();
            var rects = new List<(int x, int y, int w, int h)>();
            foreach (var g in glyphs)
            {
                Fields(g, "character", "x", "y", "width", "height", "shift", "offset", "kerning");
                string ch = Text(g, "character");
                Require(ch.Length == 1 && Required.Contains(ch[0]), "Only precomposed required Polish glyphs supported");
                Require(codes.Add(ch[0]), "Duplicate new glyph");
                Require(!old.Contains((int)ch[0]), "Collision with old glyph (must be preserved)");
                int x = Number(g, "x", 0, 65535), y = Number(g, "y", 0, 65535);
                int w = Number(g, "width", 1, 65535), h = Number(g, "height", 1, 65535);
                Number(g, "shift", short.MinValue, short.MaxValue); Number(g, "offset", short.MinValue, short.MaxValue);
                Require(x + w <= width && y + h <= height, "Glyph outside atlas");
                Require(!rects.Any(r => x < r.x + r.w && x + w > r.x && y < r.y + r.h && y + h > r.y), "Overlapping bitmap rectangles");
                rects.Add((x, y, w, h));
                bool visible = false;
                for (int yy = y; yy < y + h; yy++)
                    for (int xx = x; xx < x + w; xx++) visible |= pixels[(yy * width + xx) * 4 + 3] != 0;
                Require(visible, "Empty/transparent glyph bitmap");
                var preceding = new HashSet<int>();
                foreach (var k in Array(g, "kerning", 0, 4096))
                {
                    Fields(k, "character", "shiftModifier");
                    int c = Number(k, "character", short.MinValue, short.MaxValue);
                    Require(preceding.Add(c), "Duplicate kerning pair");
                    Number(k, "shiftModifier", short.MinValue, short.MaxValue);
                    int unsigned = unchecked((ushort)(short)c);
                    Require(old.Contains(unsigned) || glyphs.Any(v => Text(v, "character") == ((char)unsigned).ToString()), "Kerning references absent glyph");
                }
            }
            Require(Required.All(c => old.Contains((int)c) || codes.Contains(c)), "Incomplete Polish coverage");
            plans.Add(new { name, preservedGlyphCount = old.Length, existingRequired = new string(Required.Where(c => old.Contains((int)c)).ToArray()),
                additions = glyphs, atlas = a, proposedRangeStart = old.Concat(codes).Min(), proposedRangeEnd = old.Concat(codes).Max(),
                placement = "UNRESOLVED_SEPARATE_PAGE", coverageComplete = true });
        }
        Require(atlases.Count == usedAtlases.Count, "Unused atlas input");
        return JsonSerializer.SerializeToElement(plans);
    }

    public static void BuildGate() => throw new InvalidOperationException(Blocker);
}
