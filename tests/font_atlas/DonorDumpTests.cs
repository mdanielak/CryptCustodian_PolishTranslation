using System;
using System.IO;
using System.Linq;
using System.Text.Json;

// Optional read-only regression against the full independently verified donor dump.
// No game parser, writes, external fonts or hardcoded donor bitmaps in the workshop.
internal static class DonorDumpTests
{
    static readonly int[] ConnectivityThresholds = { 16, 32, 64, 128 };
    static readonly System.Collections.Generic.Dictionary<string, string> VariantBHashes = new System.Collections.Generic.Dictionary<string, string>
    {
        ["Nerko/ł"] = "c5384f18c8c5a2360d05013245331fe62e1ed5ee9a8b996939e41f8dd0a0b32a",
        ["Nerko/Ł"] = "7b7b9dd875d440a0d26a4b635fed6d9cf7fa76339806fb5854ab60934b8dd983",
        ["NerkoLarge/ł"] = "17abf4247ff79b793aecbbb48bf1c80ad9ff5b224d5569d592da23676e816d85",
        ["NerkoLarge/Ł"] = "42814948c718495e4c1fe0cc19aed947a81993dfeece076506595141778a3e60",
        ["NerkoLarge2/ł"] = "51a525d039e544269820681651a7abab720b4c1bdbd6fa56643c9a2717d0c4a7",
        ["NerkoLarge2/Ł"] = "368bf89ab1eb29fd96da51033a1dd21c6130a29017b0daaec26b9fb02c8aac63",
        ["NerkoSmall/ł"] = "47c555a80bb5525cae686a6c6bc26bac9ad6612ce5707516ca9d528e5288b487",
        ["NerkoSmall/Ł"] = "112f60f208273346a642081d263833113531fbda504222051be79ac56068cae3"
    };

    static System.Collections.Generic.List<System.Collections.Generic.List<int>> Components(FontAtlasCore.Bitmap image, int threshold)
    {
        var result = new System.Collections.Generic.List<System.Collections.Generic.List<int>>();
        var seen = new bool[image.W * image.H];
        for (int seed = 0; seed < seen.Length; seed++)
        {
            if (seen[seed] || image.Pixels[seed * 4 + 3] < threshold) continue;
            var component = new System.Collections.Generic.List<int>();
            var queue = new System.Collections.Generic.Queue<int>();
            queue.Enqueue(seed); seen[seed] = true;
            while (queue.Count != 0)
            {
                int p = queue.Dequeue(); component.Add(p);
                int px = p % image.W, py = p / image.W;
                for (int dy = -1; dy <= 1; dy++) for (int dx = -1; dx <= 1; dx++)
                {
                    int x = px + dx, y = py + dy;
                    if (x < 0 || y < 0 || x >= image.W || y >= image.H) continue;
                    int next = y * image.W + x;
                    if (!seen[next] && image.Pixels[next * 4 + 3] >= threshold)
                    { seen[next] = true; queue.Enqueue(next); }
                }
            }
            result.Add(component);
        }
        return result;
    }

    internal static object VerifyLStrokeComposition(FontAtlasCore.Glyph basis, FontAtlasCore.Composed result)
    {
        var mark = result.StrokeMark;
        FontAtlasCore.Need(mark != null, "DUMP_LSTROKE_MARK");
        FontAtlasCore.Need(Components(mark, 1).Count == 1 && Components(mark, 128).Count == 1,
            "DUMP_LSTROKE_MARK_CONNECTIVITY");
        FontAtlasCore.Need(Components(result.Glyph.Image, 1).Count == 1, "DUMP_LSTROKE_RESULT_CONNECTIVITY");
        int outsideFootprint = 0, opaqueBase = 0, aaIntersection = 0;
        int strongOverlap = 0, added = 0;
        long leftMass = 0, rightMass = 0;
        var baseBounds = FontAtlasCore.Bounds(basis.Image);
        int stemY = baseBounds.y + baseBounds.h / 2;
        int run = 0, stem = 0, stemX = -1;
        for (int x = 0; x < basis.Image.W; x++)
        {
            run = basis.Image.Ink(x, stemY) ? run + 1 : 0;
            if (run > stem) { stem = run; stemX = x - run + 1; }
        }
        FontAtlasCore.Need(stem > 0, "DUMP_LSTROKE_STEM");
        for (int y = 0; y < mark.H; y++) for (int x = 0; x < mark.W; x++)
        {
            int alpha = mark.Pixels[(y * mark.W + x) * 4 + 3];
            if (alpha == 0) continue;
            int bx = result.StrokeX + x - result.StrokeBaseX, by = result.StrokeY + y - result.StrokeBaseY;
            int ba = bx >= 0 && by >= 0 && bx < basis.Image.W && by < basis.Image.H ? basis.Image.Pixels[(by * basis.Image.W + bx) * 4 + 3] : 0;
            if (alpha >= 128 && ba >= 128) strongOverlap++;
            if (ba == 0) added++;
            if (result.StrokeX + x < result.StrokeBaseX + stemX) leftMass += alpha;
            if (result.StrokeX + x >= result.StrokeBaseX + stemX + stem) rightMass += alpha;
        }
        int leftExtent = result.StrokeBaseX + stemX - result.StrokeX;
        int rightExtent = result.StrokeX + mark.W - (result.StrokeBaseX + stemX + stem);
        FontAtlasCore.Need(leftExtent > 0 && rightExtent > 0 && Math.Abs(leftExtent - rightExtent) <= 1,
            "DUMP_LSTROKE_PROTRUSION");
        FontAtlasCore.Need(leftMass + rightMass > 0 &&
            (double)Math.Abs(leftMass - rightMass) / (leftMass + rightMass) <= 0.25, "DUMP_LSTROKE_IMBALANCE");
        FontAtlasCore.Need(strongOverlap > 0 && added > 0, "DUMP_LSTROKE_STRONG_OVERLAP");
        FontAtlasCore.Need(result.StrokeY == result.StrokeBaseY + stemY - mark.H / 2 &&
            result.StrokeY >= result.StrokeBaseY + baseBounds.y + 1 &&
            result.StrokeY + mark.H <= result.StrokeBaseY + baseBounds.y + baseBounds.h - 1 &&
            result.StrokeX >= 1 && result.StrokeX + mark.W <= result.Glyph.Image.W - 1, "DUMP_LSTROKE_CLIPPING_ANCHOR");

        for (int y = 0; y < basis.Image.H; y++) for (int x = 0; x < basis.Image.W; x++)
        {
            int bi = (y * basis.Image.W + x) * 4;
            int rx = result.StrokeBaseX + x, ry = result.StrokeBaseY + y;
            int ri = (ry * result.Glyph.Image.W + rx) * 4;
            int mx = rx - result.StrokeX, my = ry - result.StrokeY;
            bool inFootprint = mx >= 0 && my >= 0 && mx < mark.W && my < mark.H && mark.Pixels[(my * mark.W + mx) * 4 + 3] != 0;
            if (!inFootprint)
            {
                outsideFootprint++;
                for (int channel = 0; channel < 4; channel++)
                    FontAtlasCore.Need(basis.Image.Pixels[bi + channel] == result.Glyph.Image.Pixels[ri + channel], "DUMP_BASE_OUTSIDE_STROKE_CHANGED");
            }
            if (basis.Image.Pixels[bi + 3] == 255)
            {
                opaqueBase++;
                for (int channel = 0; channel < 4; channel++)
                    FontAtlasCore.Need(basis.Image.Pixels[bi + channel] == result.Glyph.Image.Pixels[ri + channel], "DUMP_OPAQUE_BASE_CHANGED");
            }
            if (!inFootprint || basis.Image.Pixels[bi + 3] == 0 || basis.Image.Pixels[bi + 3] == 255) continue;
            aaIntersection++;
            int mi = (my * mark.W + mx) * 4, baseAlpha = basis.Image.Pixels[bi + 3], markAlpha = mark.Pixels[mi + 3];
            int alphaNumerator = baseAlpha * 255 + markAlpha * (255 - baseAlpha);
            for (int channel = 0; channel < 3; channel++)
            {
                int expected = (basis.Image.Pixels[bi + channel] * baseAlpha * 255 + mark.Pixels[mi + channel] * markAlpha * (255 - baseAlpha) + alphaNumerator / 2) / alphaNumerator;
                FontAtlasCore.Need(result.Glyph.Image.Pixels[ri + channel] == expected, "DUMP_BASE_OVER_SLASH_RGB");
            }
            FontAtlasCore.Need(result.Glyph.Image.Pixels[ri + 3] == (alphaNumerator + 127) / 255, "DUMP_BASE_OVER_SLASH_ALPHA");
        }
        FontAtlasCore.Need(outsideFootprint > 0 && opaqueBase > 0 && aaIntersection > 0, "DUMP_LSTROKE_COMPOSITION_COVERAGE");
        VerifyThresholdConnectivity(basis, result);
        return new { outsideFootprint, opaqueBase, aaIntersection, strongOverlap, added, leftExtent, rightExtent,
            leftMass, rightMass, imbalance = (double)Math.Abs(leftMass - rightMass) / (leftMass + rightMass),
            thresholds = ConnectivityThresholds.Select(q => new { threshold = q,
                componentPixelCounts = Components(result.Glyph.Image, q).Select(c => c.Count).OrderByDescending(n => n).ToArray(),
                everyComponentContainsOriginalBaseInk = true }).ToArray() };
    }

    internal static void VerifyThresholdConnectivity(FontAtlasCore.Glyph basis, FontAtlasCore.Composed result)
    {
        foreach (int threshold in ConnectivityThresholds)
            foreach (var component in Components(result.Glyph.Image, threshold))
                FontAtlasCore.Need(component.Any(p => {
                    int x = p % result.Glyph.Image.W - result.StrokeBaseX, y = p / result.Glyph.Image.W - result.StrokeBaseY;
                    return x >= 0 && y >= 0 && x < basis.Image.W && y < basis.Image.H && basis.Image.Pixels[(y * basis.Image.W + x) * 4 + 3] >= threshold;
                }), "DUMP_LSTROKE_THRESHOLD_CONNECTIVITY threshold=" + threshold);
    }
    public static void Run(string directory, Action<string, Action> test, bool review = false, string baselineBundle = null)
    {
        using var doc = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(directory, "donors.json")));
        var root = doc.RootElement;
        FontAtlasCore.Need(root.GetProperty("schema").GetString() == "cc-font-donors/v1" &&
            root.GetProperty("sourceSha256").GetString() == "15e2c8ef57f4c5f599589b5d15021281a11b73757be54d0bbf739d57dd280b99", "DUMP_SOURCE");
        FontAtlasCore.Need(root.GetProperty("fonts").EnumerateArray().Select(f => f.GetProperty("name").GetString()).OrderBy(n => n)
            .SequenceEqual(FontAtlasCore.Names.OrderBy(n => n)), "DUMP_FOUR_FONTS");
        if (baselineBundle != null)
        {
            FontBuilderCore.CheckHash(File.ReadAllBytes(Path.Combine(baselineBundle, "manifest.json")), "519f8ab82cf08ca88aea3b52cacbf5d9a804e0ea9be80dd5fde63401f02bbad0");
            FontBuilderCore.CheckHash(File.ReadAllBytes(Path.Combine(baselineBundle, "report.json")), "02b2b719b0e11112156663837ed4776cdfd9d22c85fe57fed968ba4a4617d726");
        }
        int verifiedStrokes = 0;
        foreach (var f in root.GetProperty("fonts").EnumerateArray())
        {
            string name = f.GetProperty("name").GetString();
            var a = f.GetProperty("atlas"); byte[] raw = File.ReadAllBytes(Path.Combine(directory, FontBuilderCore.Leaf(a.GetProperty("file").GetString())));
            FontBuilderCore.CheckHash(raw, a.GetProperty("sha256").GetString());
            var atlas = new FontAtlasCore.Bitmap(a.GetProperty("width").GetInt32(), a.GetProperty("height").GetInt32(), raw);
            var glyphs = f.GetProperty("glyphs").EnumerateArray().ToArray();
            FontAtlasCore.Glyph Get(char c)
            {
                var g = glyphs.Single(g => g.GetProperty("Character").GetInt32() == c);
                var image = FontAtlasCore.Crop(atlas, g.GetProperty("SourceX").GetInt32(), g.GetProperty("SourceY").GetInt32(), g.GetProperty("SourceWidth").GetInt32(), g.GetProperty("SourceHeight").GetInt32());
                FontBuilderCore.CheckHash(image.Pixels, g.GetProperty("cropRgbaSha256").GetString());
                return new FontAtlasCore.Glyph { Character = c, Image = image, Shift = g.GetProperty("Shift").GetInt32(), Offset = g.GetProperty("Offset").GetInt32(), Kerning = JsonSerializer.SerializeToElement(new object[0]) };
            }
            foreach (char c in "ąćęłńśźżĄĆĘŁŃŚŹŻ") test("full donor dump " + name + " " + c, () => {
                var basis = Get(FontAtlasCore.Base(c)); string donor = "ąęĄĘ".Contains(c) ? "," : "/";
                var source = Get(donor[0]);
                var mark = source.Image;
                if ("ćńśźĆŃŚŹ".Contains(c))
                {
                    bool upper = char.IsUpper(c); donor = upper ? "Ó" : "ó";
                    mark = FontAtlasCore.Acute(Get(donor[0]), Get(upper ? 'O' : 'o'));
                }
                else if ("żŻ".Contains(c)) { donor = "."; mark = FontAtlasCore.Dot(Get('.'), false); }
                int height = glyphs.Max(g => g.GetProperty("SourceHeight").GetInt32());
                float em = f.GetProperty("metrics").GetProperty("EmSize").GetSingle();
                var result = FontAtlasCore.Compose(c, basis, mark, donor, height, em);
                var again = FontAtlasCore.Compose(c, basis, mark, donor, height, em);
                FontAtlasCore.Need(result.Glyph.Image.Pixels.SequenceEqual(again.Glyph.Image.Pixels) && JsonSerializer.Serialize(result.Provenance) == JsonSerializer.Serialize(again.Provenance), "DUMP_DETERMINISM");
                // Emit evidence before assertions, so a failed gate remains diagnosable.
                if (review) Console.WriteLine("DUMP_REVIEW " + JsonSerializer.Serialize(new { name, character = c.ToString(), width = result.Glyph.Image.W, height = result.Glyph.Image.H, rgba = result.Glyph.Image.Pixels, provenance = result.Provenance,
                    stroke = result.StrokeMark == null ? null : new { width = result.StrokeMark.W, height = result.StrokeMark.H, rgba = result.StrokeMark.Pixels,
                        x = result.StrokeX, y = result.StrokeY, baseX = result.StrokeBaseX, baseY = result.StrokeBaseY } }));
                if ("łŁ".Contains(c))
                {
                    var gates = VerifyLStrokeComposition(basis, result);
                    if (review) Console.WriteLine("DUMP_LSTROKE_GATES " + JsonSerializer.Serialize(new { name, character = c.ToString(), gates }));
                    // Mandatory even without --baseline-bundle; never regenerate these pins.
                    FontAtlasCore.Need(VariantBHashes.TryGetValue(name + "/" + c, out string expected) &&
                        FontBuilderCore.Hash(result.Glyph.Image.Pixels) == expected, "VARIANT_B_BITMAP_HASH");
                }
                else
                {
                    int shift = basis.Offset - result.Glyph.Offset;
                    for (int y = 0; y < basis.Image.H; y++) for (int x = 0; x < basis.Image.W; x++) if (basis.Image.Ink(x, y))
                        for (int channel = 0; channel < 4; channel++) FontAtlasCore.Need(basis.Image.Pixels[(y * basis.Image.W + x) * 4 + channel] == result.Glyph.Image.Pixels[(y * result.Glyph.Image.W + x + shift) * 4 + channel], "DUMP_BASE_CHANGED");
                }
                if (baselineBundle != null)
                {
                    var manifest = FontBuilderCore.Parse(File.ReadAllBytes(Path.Combine(baselineBundle, "manifest.json")));
                    var oldFont = manifest.GetProperty("fonts").EnumerateArray().Single(x => x.GetProperty("name").GetString() == name);
                    var oldAtlas = oldFont.GetProperty("atlas");
                    var bytes = File.ReadAllBytes(Path.Combine(baselineBundle, FontBuilderCore.Leaf(oldAtlas.GetProperty("file").GetString())));
                    FontBuilderCore.CheckHash(bytes, oldAtlas.GetProperty("sha256").GetString());
                    var old = oldFont.GetProperty("glyphs").EnumerateArray().Single(x => x.GetProperty("character").GetString() == c.ToString());
                    var bitmap = new FontAtlasCore.Bitmap(oldAtlas.GetProperty("width").GetInt32(), oldAtlas.GetProperty("height").GetInt32(), bytes);
                    var crop = FontAtlasCore.Crop(bitmap, old.GetProperty("x").GetInt32(), old.GetProperty("y").GetInt32(), old.GetProperty("width").GetInt32(), old.GetProperty("height").GetInt32());
                    if ("łŁ".Contains(c))
                    {
                        FontAtlasCore.Need(crop.W == result.Glyph.Image.W && crop.H == result.Glyph.Image.H, "VARIANT_B_DIMENSIONS");
                        // Variant B changes only raster data: the already accepted
                        // bearing and advance from the baseline bundle are immutable.
                        FontAtlasCore.Need(result.Glyph.Offset == old.GetProperty("offset").GetInt32(), "VARIANT_B_OFFSET");
                        FontAtlasCore.Need(result.Glyph.Shift == old.GetProperty("shift").GetInt32(), "VARIANT_B_SHIFT");
                    }
                    else
                    {
                        FontAtlasCore.Need(crop.W == result.Glyph.Image.W && crop.H == result.Glyph.Image.H && crop.Pixels.SequenceEqual(result.Glyph.Image.Pixels), "BASELINE_BITMAP_CHANGED");
                        FontAtlasCore.Need(result.Glyph.Offset == old.GetProperty("offset").GetInt32(), "BASELINE_OFFSET");
                        FontAtlasCore.Need(result.Glyph.Shift == old.GetProperty("shift").GetInt32(), "BASELINE_SHIFT");
                    }
                    var oldReport = FontBuilderCore.Parse(File.ReadAllBytes(Path.Combine(baselineBundle, "report.json")));
                    var oldProvenance = oldReport.GetProperty("provenance").EnumerateArray().Single(x => x.GetProperty("name").GetString() == name)
                        .GetProperty("additions").EnumerateArray().Single(x => x.GetProperty("character").GetString() == c.ToString());
                    var current = JsonSerializer.SerializeToElement(result.Provenance);
                    foreach (string key in "łŁ".Contains(c) ? new[] { "baseX", "baseY" } : new[] { "geometry", "donorTransform", "markSha256", "resultSha256", "baseX", "baseY" })
                        FontAtlasCore.Need(JsonSerializer.Serialize(oldProvenance.GetProperty(key)) == JsonSerializer.Serialize(current.GetProperty(key)), "BASELINE_GEOMETRY " + key);
                }
                if ("łŁ".Contains(c)) verifiedStrokes++;
            });
        }
        test("all eight variant B hashes verified", () => FontAtlasCore.Need(verifiedStrokes == 8, "VARIANT_B_EIGHT_REQUIRED"));
    }
}
