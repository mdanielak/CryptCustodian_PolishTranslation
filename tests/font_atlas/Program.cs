using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;
using UndertaleModLib.Models;
using B = FontAtlasCore.Bitmap;
using G = FontAtlasCore.Glyph;

internal static class Program
{
    static int passed, failed;
    static void Assert(bool condition, string why = "assertion") { if (!condition) throw new Exception(why); }
    static void Test(string name, Action run)
    {
        try { run(); passed++; Console.WriteLine("PASS " + name); }
        catch (Exception e) { failed++; Console.WriteLine("FAIL " + name + ": " + e.Message); }
    }
    static void Refuse(Action run, string code)
    {
        try { run(); } catch (InvalidDataException e) { Assert(e.Message.Contains(code), e.Message); return; }
        throw new Exception("Expected refusal " + code);
    }
    static B Pixels(int w, int h, Func<int, int, bool> ink, byte alpha = 255)
    {
        var p = new byte[w * h * 4];
        for (int y = 0; y < h; y++) for (int x = 0; x < w; x++) if (ink(x, y))
        { int i = (y * w + x) * 4; p[i] = 91; p[i + 1] = 123; p[i + 2] = 217; p[i + 3] = alpha; }
        return new B(w, h, p);
    }
    static G Glyph(char c, B image = null, int offset = -1, int shift = 13) => new G { Character = c,
        Image = image ?? ("lL".Contains(c) ? Pixels(12, 24, (x, y) => x >= 5 && x <= 6 && y >= 5 && y <= 21) : Pixels(12, 24, (x, y) => x >= 3 && x <= 8 && y >= 8 && y <= 15)),
        Shift = shift, Offset = offset, Kerning = JsonSerializer.SerializeToElement(new[] { new { character = 65, shiftModifier = -1 } }) };
    static G AcuteDonor() => Glyph('ó', Pixels(12, 24, (x, y) =>
        (x >= 3 && x <= 8 && y >= 8 && y <= 15) || (y >= 2 && y <= 4 && x == 9 - y)));
    static B Acute() => FontAtlasCore.Acute(AcuteDonor(), Glyph('o'));
    static B Dot() => FontAtlasCore.Dot(Glyph('.', Pixels(4, 4, (x, y) => x >= 1 && x <= 2 && y >= 1 && y <= 2)), false);
    static B Comma() => Pixels(6, 8, (x, y) => Math.Abs(x - (4 - y * 0.45)) <= (y < 5 ? 1.2 : 0.6), 180);
    static B Slash() => Pixels(8, 18, (x, y) => Math.Abs(x - (6 - y * 0.3)) <= 0.8, 210);
    static FontAtlasCore.Composed Compose(char c) => FontAtlasCore.Compose(c, Glyph(FontAtlasCore.Base(c)),
        "ćńśźĆŃŚŹ".Contains(c) ? Acute() : "ąęĄĘ".Contains(c) ? Comma() : "łŁ".Contains(c) ? Slash() : Dot(),
        "ąęĄĘ".Contains(c) ? "," : "łŁ".Contains(c) ? "/" : "synthetic-fixture", 24);
    static void BasePreserved(G old, G added, int optical = 0, int? expectedShift = null)
    {
        int translation = old.Offset + optical - added.Offset;
        for (int y = 0; y < old.Image.H; y++) for (int x = 0; x < old.Image.W; x++) if (old.Image.Ink(x, y))
        {
            int nextX = x + translation;
            Assert(old.Offset + x + optical == added.Offset + nextX, "unexpected pen-space translation");
            for (int c = 0; c < 4; c++) Assert(old.Image.Pixels[(y * old.Image.W + x) * 4 + c] == added.Image.Pixels[(y * added.Image.W + nextX) * 4 + c], "base pixel/baseline changed");
        }
        Assert((expectedShift ?? old.Shift) == added.Shift, "advance changed");
        Assert(old.Kerning.GetRawText() == added.Kerning.GetRawText(), "kerning changed");
    }
    static int Main(string[] args)
    {
        Test("local codec reflection/IL audit", () => CodecAudit.Run(args.Contains("--codec-il")));
        Bz2QoiTests.Run(Test);
        Test("actual atlas CSX compiles with local CLI references; NOT executed", () => ScriptCompilation.Run());
        // In-memory composition, diagnostics and synthetic managed codecs. No game access.
        foreach (uint scaled in new[] { 0u, 1u, 7u, uint.MaxValue })
        for (int bits = 0; bits < 8; bits++)
        {
            int mask = bits;
            Test("texture gate independent of raw Scaled=" + scaled + " conditions=" + mask, () => {
                bool external = (mask & 1) != 0, loaded = (mask & 2) == 0;
                uint mips = (mask & 4) != 0 ? 3u : 0u;
                var d = new FontAtlasUmt.TextureDiagnostic("Nerko", 4, 12, 2, external, loaded, scaled, mips);
                var expected = new List<string>();
                if (external) expected.Add("TEXTURE_EXTERNAL: TextureExternal=true; required false");
                if (!loaded) expected.Add("TEXTURE_NOT_LOADED: TextureLoaded=false; required true");
                if (mips != 0) expected.Add("TEXTURE_GENERATED_MIPS: GeneratedMips=3; required 0");
                Assert(d.reasons.SequenceEqual(expected));
                if (mask == 0) d.RequireAllowed();
                else
                {
                    try { d.RequireAllowed(); throw new Exception("gate weakened"); }
                    catch (InvalidDataException e) { Assert(e.Message == string.Join("; ", expected)); }
                }
                var json = FontBuilderCore.Parse(JsonSerializer.SerializeToUtf8Bytes(d));
                Assert(json.GetProperty("font").GetString() == "Nerko");
                Assert(json.GetProperty("fontIndex").GetInt32() == 4 && json.GetProperty("tpagIndex").GetInt32() == 12 && json.GetProperty("txtrIndex").GetInt32() == 2);
                Assert(json.GetProperty("TextureExternal").GetBoolean() == external && json.GetProperty("TextureLoaded").GetBoolean() == loaded);
                Assert(json.GetProperty("Scaled").GetUInt32() == scaled && json.GetProperty("GeneratedMips").GetUInt32() == mips);
                Assert(json.GetProperty("reasons").EnumerateArray().Select(r => r.GetString()).SequenceEqual(expected));
            });
        }
        Test("texture diagnostics retain full uint values", () => {
            var d = new FontAtlasUmt.TextureDiagnostic("NerkoSmall", 5, 6, 7, false, true, uint.MaxValue, uint.MaxValue);
            var json = FontBuilderCore.Parse(JsonSerializer.SerializeToUtf8Bytes(d));
            Assert(json.GetProperty("Scaled").GetUInt32() == uint.MaxValue && json.GetProperty("GeneratedMips").GetUInt32() == uint.MaxValue);
            Assert(d.reasons.Length == 1); Refuse(d.RequireAllowed, "TEXTURE_GENERATED_MIPS: GeneratedMips=4294967295");
        });
        foreach (uint scaled in new[] { 0u, 1u, 7u, uint.MaxValue })
        for (int variant = 0; variant <= 6; variant++)
        {
            int change = variant;
            Test("identity geometry independent of raw Scaled=" + scaled + " change=" + change, () => {
                var d = new FontAtlasUmt.TextureDiagnostic("Nerko", 0, 0, 0, false, true, scaled, 0);
                d.RequireAllowed();
                var t = new UndertaleTexturePageItem { SourceX = 17, SourceY = 23,
                    SourceWidth = 32, TargetWidth = 32, BoundingWidth = 32,
                    SourceHeight = 48, TargetHeight = 48, BoundingHeight = 48, TargetX = 0, TargetY = 0 };
                switch (change)
                {
                    case 1: t.TargetX = 1; break;
                    case 2: t.TargetY = 1; break;
                    case 3: t.TargetWidth = 64; break;
                    case 4: t.TargetHeight = 96; break;
                    case 5: t.BoundingWidth = 64; break;
                    case 6: t.BoundingHeight = 96; break;
                }
                if (change == 0) FontAtlasUmt.RequireIdentityLayout(t);
                else Refuse(() => FontAtlasUmt.RequireIdentityLayout(t), "TPAG_NONIDENTITY_LAYOUT");
                Assert(t.SourceX == 17 && t.SourceY == 23 && t.SourceWidth == 32 && t.SourceHeight == 48);
                Assert(d.Scaled == scaled);
            });
        }
        Test("per-font texture diagnostics survive refusal and Polish JSON round-trip", () => {
            var textures = FontAtlasCore.Names.Select((n, i) => new FontAtlasUmt.TextureDiagnostic(n, i, 10 + i, 2, false, false, 0, 0)).ToArray();
            var diagnostics = new List<object>();
            foreach (var d in textures)
                try { d.RequireAllowed(); }
                catch (InvalidDataException e) {
                    foreach (char c in FontBuilderCore.Required) diagnostics.Add(new { font = d.font, character = c.ToString(), status = "refused-font", reason = e.Message });
                }
            byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(new { textureDiagnostics = textures, diagnostics });
            var parsed = FontBuilderCore.Parse(bytes);
            Assert(new UTF8Encoding(false, true).GetBytes(new UTF8Encoding(false, true).GetString(bytes)).SequenceEqual(bytes));
            Assert(parsed.GetProperty("textureDiagnostics").GetArrayLength() == 4);
            Assert(parsed.GetProperty("diagnostics").GetArrayLength() == 72);
            foreach (string name in FontAtlasCore.Names)
                Assert(string.Concat(parsed.GetProperty("diagnostics").EnumerateArray().Where(d => d.GetProperty("font").GetString() == name)
                    .Select(d => d.GetProperty("character").GetString())) == FontBuilderCore.Required);
        });
        Test("alpha bounds ignore RGB under alpha zero", () => {
            var p = new byte[5 * 7 * 4]; for (int i = 0; i < p.Length; i += 4) p[i] = 255;
            p[(3 * 5 + 2) * 4 + 3] = 1;
            Assert(FontAtlasCore.Bounds(new B(5, 7, p)) == (2, 3, 1, 1));
        });
        Test("empty alpha refused", () => Refuse(() => FontAtlasCore.Bounds(Pixels(3, 3, (x, y) => false)), "EMPTY_ALPHA"));
        Test("bitmap length refused", () => Refuse(() => new B(2, 2, new byte[15]), "BITMAP_LENGTH"));
        Test("dimensions refused", () => Refuse(() => new B(0, 1, new byte[0]), "BITMAP_DIMENSIONS"));
        Test("crop exact RGBA and top-down rows", () => {
            var b = Pixels(7, 9, (x, y) => x == 2 && y == 3, 37);
            var c = FontAtlasCore.Crop(b, 1, 2, 3, 4);
            Assert(FontAtlasCore.Bounds(c) == (1, 1, 1, 1));
            Assert(c.Pixels[(1 * 3 + 1) * 4] == 91 && c.Pixels[(1 * 3 + 1) * 4 + 3] == 37);
        });
        Test("crop overflow refused", () => Refuse(() => FontAtlasCore.Crop(Dot(), int.MaxValue, 0, 2, 2), "CROP_BOUNDS"));
        Test("constructor does not alias caller", () => { var p = new byte[] { 1, 2, 3, 255 }; var b = new B(1, 1, p); p[3] = 0; Assert(b.Ink(0, 0)); });
        Test("BGRA to straight RGBA copies channels, alpha and hidden RGB", () => {
            byte[] input = { 11, 22, 33, 127, 44, 55, 66, 0 };
            var before = (byte[])input.Clone(); var rgba = FontAtlasCore.FromBgra(2, 1, input);
            Assert(rgba.Pixels.SequenceEqual(new byte[] { 33, 22, 11, 127, 66, 55, 44, 0 }));
            Assert(input.SequenceEqual(before));
        });
        Test("acute isolated with exact unchanged body", () => { var a = Acute(); Assert(a.W == 3 && a.H == 3 && a.Ink(2, 0) && a.Ink(0, 2)); });
        Test("independent acute accepts mismatched body with warning", () => {
            var plain = Glyph('o'); plain.Image.Pixels[(10 * 12 + 5) * 4] = 0;
            var mark = FontAtlasCore.Acute(AcuteDonor(), plain, out var diagnostic);
            Assert(mark.Pixels.SequenceEqual(Acute().Pixels));
            Assert(JsonSerializer.Serialize(diagnostic).Contains("ACUTE_BODY_NOT_EXACT_BASE"));
        });
        Test("plain body placement does not move independent acute", () => Assert(FontAtlasCore.Acute(AcuteDonor(),
            Glyph('o', Pixels(12, 24, (x, y) => x >= 3 && x <= 8 && y >= 9 && y <= 16))).Pixels.SequenceEqual(Acute().Pixels)));
        Test("independent acute advance mismatch is diagnostic only", () => {
            FontAtlasCore.Acute(AcuteDonor(), Glyph('o', shift: 12), out var diagnostic);
            Assert(JsonSerializer.Serialize(diagnostic).Contains("ACUTE_ADVANCE_MISMATCH"));
        });
        foreach (bool diagonal in new[] { false, true })
            Test("acute contact including alpha=1 diagonal=" + diagonal, () => {
                var donor = AcuteDonor();
                for (int y = 5; y <= 7; y++) donor.Image.Pixels[(y * 12 + (diagonal ? 9 - y : 5)) * 4 + 3] = 1;
                Refuse(() => FontAtlasCore.Acute(donor, Glyph('o')), "ACUTE_COMPONENT_COUNT_OR_CONTACT");
            });
        Test("acute side component refused", () => Refuse(() => FontAtlasCore.Acute(Glyph('ó', Pixels(24, 24,
            (x, y) => (x >= 3 && x <= 8 && y >= 8 && y <= 15) || (y >= 2 && y <= 4 && x == 23 - y))), Glyph('o')), "ACUTE_BAD_POSITION"));
        Test("acute remote upper component refused", () => Refuse(() => FontAtlasCore.Acute(Glyph('ó', Pixels(12, 24,
            (x, y) => (x >= 3 && x <= 8 && y >= 12 && y <= 19) || (y >= 1 && y <= 3 && x == 9 - y))), Glyph('o')), "ACUTE_BAD_POSITION"));
        Test("acute excessive alpha area refused", () => Refuse(() => FontAtlasCore.Acute(Glyph('ó', Pixels(12, 24,
            (x, y) => (y >= 8 && y <= 15 && (x == 3 || x == 8 || y == 15)) ||
                (y >= 2 && y <= 5 && x >= 3 && x <= 8 && !(y == 2 && x == 3)))), Glyph('o')), "ACUTE_AREA_TOO_LARGE"));
        Test("acute excessive height refused", () => Refuse(() => FontAtlasCore.Acute(Glyph('ó', Pixels(12, 24,
            (x, y) => (x >= 3 && x <= 8 && y >= 8 && y <= 15) || (y >= 1 && y <= 5 && x == 9 - y))), Glyph('o')), "MARK_TOO_LARGE"));
        Test("acute empty donor refused", () => Refuse(() => FontAtlasCore.Acute(Glyph('ó', Pixels(12, 24, (x, y) => false)), Glyph('o')), "EMPTY_ALPHA"));
        Test("acute ambiguous third alpha component refused", () => {
            var donor = AcuteDonor(); donor.Image.Pixels[3] = 1;
            Refuse(() => FontAtlasCore.Acute(donor, Glyph('o')), "ACUTE_COMPONENT_COUNT_OR_CONTACT");
        });
        Test("joined mark refused", () => Refuse(() => FontAtlasCore.Upper(Pixels(10, 15, (x, y) => x == 4 && y >= 2)), "NO_UNAMBIGUOUS_UPPER_COMPONENT"));
        Test("three row bands refused", () => Refuse(() => FontAtlasCore.Upper(Pixels(10, 15, (x, y) => x == 4 && (y == 1 || y == 4 || y >= 8))), "NO_UNAMBIGUOUS_UPPER_COMPONENT"));
        Test("disconnected upper component refused", () => Refuse(() => FontAtlasCore.Upper(Pixels(10, 15,
            (x, y) => (y <= 1 && (x == 1 || x == 7)) || (y >= 5 && x >= 3 && x <= 6))), "AMBIGUOUS_COMPONENT"));
        Test("grave is not acute", () => Refuse(() => FontAtlasCore.Acute(Glyph('ó', Pixels(12, 24,
            (x, y) => (x >= 3 && x <= 8 && y >= 8 && y <= 15) || (y >= 2 && y <= 4 && x == y + 2))), Glyph('o')), "ACUTE_NOT_RISING_RIGHT"));
        Test("real period crop", () => { var dot = Dot(); Assert(dot.W == 2 && dot.H == 2); });
        Test("i dot isolation", () => {
            var d = FontAtlasCore.Dot(Glyph('i', Pixels(5, 18, (x, y) => x >= 2 && x <= 3 && (y == 2 || y == 3 || y >= 7))), true);
            Assert(d.W == 2 && d.H == 2);
        });
        Test("elongated period refused", () => Refuse(() => FontAtlasCore.Dot(Glyph('.', Pixels(10, 2, (x, y) => true)), false), "DOT_NOT_COMPACT"));
        foreach (char ch in "ąćęłńśźżĄĆĘŁŃŚŹŻ")
        {
            char c = ch;
            Test("baseline/base/metrics preserved " + c, () => {
                var result = Compose(c); BasePreserved(Glyph(FontAtlasCore.Base(c)), result.Glyph,
                    c == 'ł' ? 1 : 0, c == 'ł' ? 14 : 13);
                Assert(result.Glyph.Image.H <= 24);
                if (!"łŁ".Contains(c)) Assert(result.Glyph.Shift == 13 && result.Glyph.Offset == -1, "nonstroke metrics changed");
            });
            Test("deterministic pixels and provenance " + c, () => {
                var a = Compose(c); var b = Compose(c);
                Assert(a.Glyph.Image.Pixels.SequenceEqual(b.Glyph.Image.Pixels));
                Assert(JsonSerializer.Serialize(a.Provenance) == JsonSerializer.Serialize(b.Provenance));
            });
        }
        Test("top alpha bounds and gap", () => {
            var r = Compose('ć').Glyph.Image;
            Assert(FontAtlasCore.Bounds(r).y == 4);
            Assert(Enumerable.Range(0, r.W).All(x => !r.Ink(x, 7)));
        });
        Test("no headroom refuses instead of moving baseline", () => Refuse(() => FontAtlasCore.Compose('ć',
            Glyph('c', Pixels(10, 12, (x, y) => x > 2 && x < 8 && y >= 2 && y < 10)), Acute(), "ó", 12), "NO_HEADROOM"));
        Test("ogonek below base but within envelope", () => {
            var g = Compose('ą').Glyph.Image; var bounds = FontAtlasCore.Bounds(g);
            Assert(bounds.y + bounds.h > 16 && bounds.y + bounds.h <= 24);
        });
        Test("ogonek clipping refuses", () => Refuse(() => FontAtlasCore.Compose('ą',
            Glyph('a', Pixels(12, 16, (x, y) => x >= 3 && x <= 8 && y >= 8)), Comma(), ",", 16), "MARK_OUTSIDE_FONT_ENVELOPE"));
        Test("stroke preserves crossing pixels and adjusts bearing", () => {
            var basis = Glyph('l', Pixels(4, 24, (x, y) => y >= 4 && y <= 21), 0);
            var result = FontAtlasCore.Compose('ł', basis, Slash(), "/", 24).Glyph;
            Assert(result.Offset < 0 && result.Image.W > basis.Image.W); BasePreserved(basis, result, 1, basis.Shift + 1);
        });
        Test("ł/Ł variant B freezes geometry parameters and keeps accepted metrics", () => {
            var basis = Glyph('l', Pixels(6, 40, (x, y) => x >= 1 && x <= 4 && y >= 4 && y <= 35), 0, 8);
            var lower = FontAtlasCore.Compose('ł', basis, Slash(), "/", 40, 64);
            var upper = FontAtlasCore.Compose('Ł', Glyph('L', basis.Image, 0, 8), Slash(), "/", 40, 64);
            var provenance = JsonSerializer.SerializeToElement(lower.Provenance).GetProperty("donorTransform").GetProperty("transform");
            Assert(provenance.GetProperty("targetDegrees").GetDouble() == 21.0);
            Assert(provenance.GetProperty("lengthFactor").GetDouble() == 0.82 && provenance.GetProperty("transverseScale").GetDouble() == 0.9);
            Assert(basis.Offset == 0 && basis.Shift == 8);
            Assert(lower.Glyph.Shift == basis.Shift + 2 && upper.Glyph.Shift >= 8);
        });
        Test("variant B colored AA base OVER slash and mutation-sensitive preservation gates", () => {
            var image = Pixels(6, 40, (x, y) => x >= 1 && x <= 4 && y >= 4 && y <= 35);
            for (int y = 4; y <= 35; y++) foreach (int x in new[] { 1, 4 })
            {
                int i = (y * image.W + x) * 4;
                image.Pixels[i] = 17; image.Pixels[i + 1] = 231; image.Pixels[i + 2] = 42; image.Pixels[i + 3] = 96;
            }
            var basis = Glyph('l', image, 0, 8);
            var result = FontAtlasCore.Compose('ł', basis, Slash(), "/", 40, 64);
            DonorDumpTests.VerifyLStrokeComposition(basis, result);
            var mark = result.StrokeMark;
            int outside = -1, opaque = -1, aa = -1;
            for (int y = 0; y < image.H; y++) for (int x = 0; x < image.W; x++)
            {
                int rx = x + result.StrokeBaseX, ry = y + result.StrokeBaseY;
                int mx = rx - result.StrokeX, my = ry - result.StrokeY;
                bool footprint = mx >= 0 && my >= 0 && mx < mark.W && my < mark.H && mark.Ink(mx, my);
                int alpha = image.Pixels[(y * image.W + x) * 4 + 3], ri = (ry * result.Glyph.Image.W + rx) * 4;
                if (!footprint && alpha == 96) outside = ri;
                if (footprint && alpha == 255) opaque = ri;
                if (footprint && alpha == 96) aa = ri;
            }
            Assert(outside >= 0 && opaque >= 0 && aa >= 0, "mutation coverage");
            foreach (var change in new[] { (outside, "DUMP_BASE_OUTSIDE_STROKE_CHANGED"), (opaque, "DUMP_OPAQUE_BASE_CHANGED"),
                (aa, "DUMP_BASE_OVER_SLASH_RGB"), (aa + 3, "DUMP_BASE_OVER_SLASH_ALPHA") })
            {
                result.Glyph.Image.Pixels[change.Item1] ^= 1;
                Refuse(() => DonorDumpTests.VerifyLStrokeComposition(basis, result), change.Item2);
                result.Glyph.Image.Pixels[change.Item1] ^= 1;
            }
            DonorDumpTests.VerifyLStrokeComposition(basis, result);
        });
        Test("narrow l retains slash AA, safe canvas and metrics", () => {
            var basis = Glyph('l', Pixels(6, 40, (x, y) => x >= 1 && x <= 4 && y >= 4 && y <= 35), 0, 8);
            var mark = Slash();
            var result = FontAtlasCore.Compose('ł', basis, mark, "/", 40, 40).Glyph;
            Assert(result.Offset < 0 && result.Image.W > basis.Image.W && result.Shift >= basis.Shift);
            Assert(result.Image.Pixels.Where((v, i) => i % 4 == 3).Any(v => v > 0 && v < 255));
            Assert(Enumerable.Range(0, result.Image.H).All(y => !result.Image.Ink(0, y) && !result.Image.Ink(result.Image.W - 1, y)));
            BasePreserved(basis, result, 1, basis.Shift + 1);
            var again = FontAtlasCore.Compose('ł', basis, mark, "/", 40, 40).Glyph;
            Assert(again.Offset == result.Offset && again.Shift == result.Shift && again.Image.Pixels.SequenceEqual(result.Image.Pixels));
        });
        foreach (int threshold in new[] { 16, 32, 64, 128 })
            Test("threshold connectivity permits inherited fringe, rejects stroke-only island at " + threshold, () => {
                var basis = Glyph('l', Pixels(7, 3, (x, y) => y == 1 && (x == 1 || x == 3)));
                basis.Image.Pixels[(1 * 7 + 2) * 4 + 3] = 1;
                var image = new B(7, 3, basis.Image.Pixels);
                image.Pixels[(1 * 7 + 4) * 4 + 3] = (byte)(threshold - 1);
                image.Pixels[(1 * 7 + 5) * 4 + 3] = (byte)(threshold - 1);
                var result = new FontAtlasCore.Composed { Glyph = Glyph('ł', image) };
                // Two inherited components at high thresholds are valid, not repaired.
                DonorDumpTests.VerifyThresholdConnectivity(basis, result);
                image.Pixels[(1 * 7 + 5) * 4 + 3] = (byte)threshold;
                Refuse(() => DonorDumpTests.VerifyThresholdConnectivity(basis, result), "DUMP_LSTROKE_THRESHOLD_CONNECTIVITY threshold=" + threshold);
            });
        foreach (char c in "łŁ")
        {
            Test("lowercase optical spacing / unchanged uppercase alpha-edge spacing " + c, () => {
                bool upper = c == 'Ł';
                var image = Pixels(16, 24, (x, y) =>
                    (x >= 2 && x <= 5 && y >= 4 && y <= 21) ||
                    (upper && x >= 2 && x <= 10 && y >= 20 && y <= 21));
                // A connected faint AA pixel is part of the uppercase foot, not padding.
                if (upper) image.Pixels[(21 * image.W + 11) * 4 + 3] = 1;
                var basis = Glyph(FontAtlasCore.Base(c), image, 1, upper ? 13 : 8);
                var before = (byte[])image.Pixels.Clone();
                string kerning = basis.Kerning.GetRawText();
                var result = FontAtlasCore.Compose(c, basis, Slash(), "/", 24);
                var g = result.Glyph; var bounds = FontAtlasCore.Bounds(g.Image);
                int right = g.Offset + bounds.x + bounds.w;
                int oldAdvance = Math.Max(basis.Shift, g.Offset + g.Image.W + 1);
                Assert(right >= basis.Offset + 1 && oldAdvance == (upper ? 18 : 19), "accepted envelope changed");
                Assert(g.Shift == (upper ? 14 : 9), "unexpected exact advance: " + g.Shift);
                Assert(g.Offset == (upper ? -2 : -1) && g.Image.W == 19 && g.Image.H == 24, "canvas/bearing changed");
                Assert(g.Shift < oldAdvance, "transparent canvas still inflates advance");
                if (upper) Assert(g.Shift == Math.Max(basis.Shift, right + 1), "uppercase changed");
                else Assert(g.Shift == basis.Shift + 1 && g.Shift < right, "lowercase must allow overhang");
                Assert(basis.Shift == (upper ? 13 : 8) && basis.Offset == 1 && basis.Character == FontAtlasCore.Base(c), "plain l/L metrics mutated");
                Assert(before.SequenceEqual(image.Pixels) && basis.Kerning.GetRawText() == kerning, "plain l/L input mutated");
                Assert(g.Kerning.GetRawText() == kerning, "stroke kerning changed");
                BasePreserved(basis, g, upper ? 0 : 1, g.Shift);
                var p = JsonSerializer.SerializeToElement(result.Provenance);
                Assert(p.GetProperty("shift").GetInt32() == g.Shift && p.GetProperty("shiftDelta").GetInt32() == g.Shift - basis.Shift);
                // More alpha-zero pixels (even with hidden RGB) must not move the next pen.
                var padded = new byte[32 * image.H * 4];
                for (int y = 0; y < image.H; y++)
                {
                    Array.Copy(image.Pixels, y * image.W * 4, padded, y * 32 * 4, image.W * 4);
                    for (int x = image.W; x < 32; x++) padded[(y * 32 + x) * 4] = 255;
                }
                var paddedResult = FontAtlasCore.Compose(c, Glyph(basis.Character, new B(32, image.H, padded), 1, basis.Shift), Slash(), "/", 24).Glyph;
                Assert(paddedResult.Shift == g.Shift && paddedResult.Offset == g.Offset, "advance depends on transparent padding");
                Assert(FontAtlasCore.Bounds(paddedResult.Image) == bounds, "padding changed visible geometry");
                for (int y = 0; y < g.Image.H; y++) for (int x = 0; x < g.Image.W; x++)
                    for (int channel = 0; channel < 4; channel++)
                        Assert(g.Image.Pixels[(y * g.Image.W + x) * 4 + channel] == paddedResult.Image.Pixels[(y * paddedResult.Image.W + x) * 4 + channel], "padding changed bitmap");
            });
        }
        foreach (float em in new[] { 22f, 26f, 48f, 64f, 72f, 96f })
            Test("lowercase metrics-only correction scales with em=" + em, () => {
                var image = Pixels(6, 40, (x, y) => x >= 1 && x <= 4 && y >= 4 && y <= 35);
                var basis = Glyph('l', image, 0, 8);
                // Same l bitmap as L: composition geometry is identical, but uppercase
                // keeps the previous spacing policy. This freezes the relative shape.
                var old = FontAtlasCore.Compose('Ł', Glyph('L', image, 0, 8), Slash(), "/", 40, em);
                var result = FontAtlasCore.Compose('ł', basis, Slash(), "/", 40, em);
                int unit = Math.Max(1, (int)Math.Floor(em / 32.0 + 0.5));
                Assert(result.Glyph.Image.W == old.Glyph.Image.W && result.Glyph.Image.H == old.Glyph.Image.H);
                Assert(result.Glyph.Image.Pixels.SequenceEqual(old.Glyph.Image.Pixels), "bitmap changed");
                Assert(result.Glyph.Offset == old.Glyph.Offset + unit && result.Glyph.Shift == basis.Shift + unit);
                BasePreserved(basis, result.Glyph, unit, basis.Shift + unit);
                var a = JsonSerializer.SerializeToElement(old.Provenance);
                var b = JsonSerializer.SerializeToElement(result.Provenance);
                foreach (string key in new[] { "geometry", "donorTransform", "markSha256", "resultSha256", "baseX", "baseY" })
                    Assert(a.GetProperty(key).GetRawText() == b.GetProperty(key).GetRawText(), "relative geometry changed: " + key);
                var moved = FontAtlasCore.Compose('ł', Glyph('l', image, 10, 8), Slash(), "/", 40, em).Glyph;
                Assert(moved.Shift == result.Glyph.Shift && moved.Offset == result.Glyph.Offset + 10,
                    "advance depends on right alpha edge in pen space");
                Assert(moved.Image.Pixels.SequenceEqual(result.Glyph.Image.Pixels));
                Assert(basis.Offset == 0 && basis.Shift == 8 && basis.Image.Pixels.SequenceEqual(image.Pixels));
            });
        Test("stroke too thick for height refused", () => Refuse(() => FontAtlasCore.Compose('ł', Glyph('l'),
            Pixels(16, 18, (x, y) => Math.Abs(x - (12 - y * 0.4)) <= 4), "/", 24, 24), "STROKE_TOO_THICK"));
        Test("stroke too long relative to height refused", () => Refuse(() => FontAtlasCore.Compose('Ł',
            Glyph('L', Pixels(24, 24, (x, y) => x >= 2 && x <= 20 && y >= 8 && y <= 15)), Slash(), "/", 24, 24), "STROKE_TOO_LONG"));
        Test("stroke missing mid stem refused", () => Refuse(() => FontAtlasCore.Compose('ł',
            Glyph('l', Pixels(8, 24, (x, y) => x == 3 && (y == 4 || y == 20))), Slash(), "/", 24, 24), "STROKE_NO_MID_STEM"));
        Test("short stroke retains top and bottom margins without clipping", () => {
            var result = FontAtlasCore.Compose('ł', Glyph('l', Pixels(5, 12, (x, y) => x == 2 || x == 3)), Slash(), "/", 12, 24).Glyph;
            Assert(result.Image.H == 12);
            Assert(Enumerable.Range(0, result.Image.W).Count(x => result.Image.Ink(x, 0)) == 2);
            Assert(Enumerable.Range(0, result.Image.W).Count(x => result.Image.Ink(x, 11)) == 2);
        });
        Test("expanded stroke canvas over 512 refused", () => Refuse(() => FontAtlasCore.Compose('ł',
            Glyph('l', Pixels(512, 24, (x, y) => x >= 510 && y >= 4 && y <= 20), 0, 512), Slash(), "/", 24, 24), "MARK_OUTSIDE_FONT_ENVELOPE"));
        Test("stroke shift overflow refused", () => Refuse(() => FontAtlasCore.Compose('ł',
            Glyph('l', Pixels(4, 24, (x, y) => y >= 4 && y <= 21), 32767, 32767), Slash(), "/", 24, 24), "METRICS_RANGE"));
        foreach (float em in new[] { float.NaN, float.PositiveInfinity, 0f, 513f })
            Test("invalid stroke EmSize " + em, () => Refuse(() => FontAtlasCore.Compose('ł', Glyph('l'), Slash(), "/", 24, em), "STROKE_METRICS"));
        Test("stroke nonpositive advance refused", () => Refuse(() => FontAtlasCore.Compose('ł', Glyph('l', shift: 0), Slash(), "/", 24, 24), "STROKE_METRICS"));
        Test("stroke refuses period instead of silently stamping nib", () => Refuse(() => FontAtlasCore.Compose('ł', Glyph('l'), Dot(), ".", 24), "WRONG_PUNCTUATION_DONOR"));
        Test("wrong base refused", () => Refuse(() => FontAtlasCore.Compose('ć', Glyph('a'), Acute(), "ó", 24), "WRONG_BASE"));
        Test("existing ó not synthesized", () => Refuse(() => FontAtlasCore.Base('ó'), "UNSUPPORTED_ADDITION"));
        Test("shift over short range refused", () => Refuse(() => FontAtlasCore.Metrics(Glyph('a', shift: 32768)), "METRICS_RANGE"));
        Test("offset under short range refused", () => Refuse(() => FontAtlasCore.Metrics(Glyph('a', offset: -32769)), "METRICS_RANGE"));
        Test("short endpoints accepted", () => { FontAtlasCore.Metrics(Glyph('a', offset: -32768, shift: 32767)); FontAtlasCore.Metrics(Glyph('a', offset: 32767, shift: -32768)); });
        Test("expanded bearing overflow refused", () => Refuse(() => FontAtlasCore.Compose('ł',
            Glyph('l', Pixels(4, 24, (x, y) => y >= 4 && y <= 21), -32768), Slash(), "/", 24), "METRICS_RANGE"));
        Test("kerning overflow refused", () => {
            var g = Glyph('a'); g.Kerning = JsonSerializer.SerializeToElement(new[] { new { character = 65, shiftModifier = 32768 } });
            Refuse(() => FontAtlasCore.Metrics(g), "Out of range");
        });
        Test("inputs unchanged after composition", () => {
            var g = Glyph('a'); var d = Comma(); byte[] a = (byte[])g.Image.Pixels.Clone(), b = (byte[])d.Pixels.Clone();
            FontAtlasCore.Compose('ą', g, d, ",", 24); Assert(a.SequenceEqual(g.Image.Pixels) && b.SequenceEqual(d.Pixels));
        });
        Test("regression: z-like hook profile rejected", () => Refuse(() => FontAtlasCore.ValidateHook(
            Pixels(9, 7, (x, y) => y == 0 || y == 6 || x == 7 - y)), "HOOK_Z"));
        Test("regression: disconnected horizontal stroke tabs rejected", () => Refuse(() => FontAtlasCore.ValidateStroke(
            Pixels(12, 6, (x, y) => (x < 4 && y == 4) || (x > 7 && y == 1))), "AMBIGUOUS_COMPONENT"));
        Test("regression: connected horizontal stroke rejected", () => Refuse(() => FontAtlasCore.ValidateStroke(
            Pixels(12, 3, (x, y) => true)), "STROKE_HORIZONTAL_TABS"));
        Test("ogonek cannot use letter z as donor", () => Refuse(() => FontAtlasCore.Compose('ą', Glyph('a'), Comma(), "z", 24), "WRONG_PUNCTUATION_DONOR"));
        Test("backslash cannot replace slash", () => Refuse(() => FontAtlasCore.Compose('ł', Glyph('l'), Slash(), "\\", 24), "WRONG_PUNCTUATION_DONOR"));
        Test("oversize donor refused before transform allocation", () => Refuse(() => FontAtlasCore.Compose('ą', Glyph('a'), Pixels(513, 8, (x, y) => x == 1), ",", 24), "DONOR_DIMENSIONS"));
        Test("stroke cannot clip vertical margins", () => Refuse(() => FontAtlasCore.Compose('ł', Glyph('l', Pixels(5, 6, (x, y) => x == 2 || x == 3)), Slash(), "/", 6), "STROKE_CLIPPING"));
        foreach (char ch in "ął") Test("premultiplied donor sampling ignores hidden RGB " + ch, () => {
            var original = ch == 'ą' ? Comma() : Slash(); var altered = new B(original.W, original.H, original.Pixels);
            for (int i = 0; i < altered.Pixels.Length; i += 4) if (altered.Pixels[i + 3] == 0)
            { altered.Pixels[i] = 255; altered.Pixels[i + 1] = 0; altered.Pixels[i + 2] = 17; }
            string donor = ch == 'ą' ? "," : "/"; var basis = Glyph(FontAtlasCore.Base(ch));
            var a = FontAtlasCore.Compose(ch, basis, original, donor, 24);
            var b = FontAtlasCore.Compose(ch, basis, altered, donor, 24);
            Assert(a.Glyph.Image.Pixels.SequenceEqual(b.Glyph.Image.Pixels));
            Assert(JsonSerializer.Serialize(a.Provenance) != JsonSerializer.Serialize(b.Provenance), "input crop SHA must retain hidden RGB");
        });
        foreach (char c in "ąęĄĘłŁ") Test("donor provenance, AA and no opaque nib " + c, () => {
            var result = Compose(c); var p = JsonSerializer.SerializeToElement(result.Provenance);
            var d = p.GetProperty("donorTransform"); bool hook = "ąęĄĘ".Contains(c);
            Assert(d.GetProperty("donorCode").GetInt32() == (hook ? 44 : 47));
            var original = hook ? Comma() : Slash(); var r = FontAtlasCore.Bounds(original);
            Assert(d.GetProperty("donorRgbaSha256").GetString() == FontBuilderCore.Hash(original.Pixels));
            Assert(d.GetProperty("cropRgbaSha256").GetString() == FontBuilderCore.Hash(FontAtlasCore.Crop(original, r.x, r.y, r.w, r.h).Pixels));
            Assert(d.GetProperty("sampling").GetString().Contains("area-premultiplied"));
            var basis = Glyph(FontAtlasCore.Base(c)); int shift = p.GetProperty("baseX").GetInt32(), added = 0;
            for (int y = 0; y < result.Glyph.Image.H; y++) for (int x = 0; x < result.Glyph.Image.W; x++)
            {
                int sx = x - shift;
                if (sx >= 0 && sx < basis.Image.W && y < basis.Image.H && basis.Image.Ink(sx, y)) continue;
                int i = (y * result.Glyph.Image.W + x) * 4; byte a = result.Glyph.Image.Pixels[i + 3]; if (a == 0) continue;
                added++; Assert(a <= (hook ? 180 : 210));
                Assert(result.Glyph.Image.Pixels[i] == 91 && result.Glyph.Image.Pixels[i + 1] == 123 && result.Glyph.Image.Pixels[i + 2] == 217);
            }
            Assert(added > 0);
        });
        Test("packed rectangles no overlap, one pixel gutters, exact bitmap round-trip", () => {
            var all = "ąćęłńśźżĄĆĘŁŃŚŹŻ".Select(Compose).ToArray(); var packed = FontAtlasCore.Pack("Nerko", all);
            var rects = packed.font.GetProperty("glyphs").EnumerateArray().ToArray();
            foreach (var r in rects)
            {
                int x = r.GetProperty("x").GetInt32(), y = r.GetProperty("y").GetInt32(), w = r.GetProperty("width").GetInt32(), h = r.GetProperty("height").GetInt32();
                char c = r.GetProperty("character").GetString()[0];
                var original = all.Single(a => a.Glyph.Character == c).Glyph;
                Assert(FontAtlasCore.Crop(packed.image, x, y, w, h).Pixels.SequenceEqual(original.Image.Pixels));
                Assert(r.GetProperty("shift").GetInt32() == original.Shift && r.GetProperty("offset").GetInt32() == original.Offset);
                Assert(r.GetProperty("kerning").GetRawText() == original.Kerning.GetRawText());
                Assert(Enumerable.Range(x - 1, w + 2).All(xx => !packed.image.Ink(xx, y - 1) && !packed.image.Ink(xx, y + h)));
                Assert(Enumerable.Range(y, h).All(yy => !packed.image.Ink(x - 1, yy) && !packed.image.Ink(x + w, yy)));
                foreach (var s in rects.Where(s => s.GetProperty("character").GetString()[0] != c))
                {
                    int sx = s.GetProperty("x").GetInt32(), sy = s.GetProperty("y").GetInt32(), sw = s.GetProperty("width").GetInt32(), sh = s.GetProperty("height").GetInt32();
                    Assert(!(x < sx + sw && sx < x + w && y < sy + sh && sy < y + h));
                }
            }
        });
        Test("packing independent of input order", () => {
            var a = "ąćęłńśźżĄĆĘŁŃŚŹŻ".Select(Compose).ToArray(); var p = FontAtlasCore.Pack("Nerko", a); var q = FontAtlasCore.Pack("Nerko", a.Reverse());
            Assert(p.image.Pixels.SequenceEqual(q.image.Pixels) && p.font.GetRawText() == q.font.GetRawText());
        });
        Test("packing wraps rows without overlap", () => {
            var g = new FontAtlasCore.Composed { Glyph = Glyph('ą', Pixels(600, 20, (x, y) => true)) };
            var h = new FontAtlasCore.Composed { Glyph = Glyph('ę', Pixels(600, 30, (x, y) => true)) };
            var p = FontAtlasCore.Pack("Nerko", new[] { g, h });
            Assert(p.font.GetProperty("glyphs")[1].GetProperty("y").GetInt32() == 22 && p.image.H == 53);
        });
        Test("duplicate atlas glyph refused", () => Refuse(() => FontAtlasCore.Pack("Nerko", new[] { Compose('ą'), Compose('ą') }), "PACK_GLYPHS"));
        Test("manifest compatibility and Polish UTF-8 round-trip", () => {
            var all = "ąćęłńśźżĄĆĘŁŃŚŹŻ".Select(Compose); var p = FontAtlasCore.Pack("Nerko", all);
            var m = JsonSerializer.SerializeToElement(new { schema = "cc-font-atlas/v1", required = FontBuilderCore.Required, fonts = new[] { p.font } });
            byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(m); var parsed = FontBuilderCore.Parse(bytes);
            Assert(parsed.GetProperty("required").GetString() == FontBuilderCore.Required);
            Assert(Encoding.UTF8.GetBytes(Encoding.UTF8.GetString(bytes)).SequenceEqual(bytes));
            FontBuilderCore.ValidateManifest(parsed, new[] { "Nerko" }, false,
                new Dictionary<string, int[]> { ["Nerko"] = "acelnoszACELNOSZóÓ.i".Select(c => (int)c).ToArray() },
                new Dictionary<string, byte[]> { ["Nerko.rgba"] = p.image.Pixels });
        });
        int dumpArg = Array.IndexOf(args, "--donor-dump");
        int baselineArg = Array.IndexOf(args, "--baseline-bundle");
        if (dumpArg >= 0) DonorDumpTests.Run(args[dumpArg + 1], Test, args.Contains("--review-dump"),
            baselineArg >= 0 ? args[baselineArg + 1] : null);
        Console.WriteLine("Generator tests: " + passed + " passed, " + failed + " failed; no data.win, generator Run or publication I-O.");
        return failed == 0 ? 0 : 1;
    }
}
