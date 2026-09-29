using System;
using System.IO;
using System.Linq;
using System.Text.Json;

// Optional read-only regression against the full independently verified donor dump.
// No game parser, writes, external fonts or hardcoded donor bitmaps in the workshop.
internal static class DonorDumpTests
{
    public static void Run(string directory, Action<string, Action> test, bool review = false, string baselineBundle = null)
    {
        using var doc = JsonDocument.Parse(File.ReadAllBytes(Path.Combine(directory, "donors.json")));
        var root = doc.RootElement;
        FontAtlasCore.Need(root.GetProperty("schema").GetString() == "cc-font-donors/v1" &&
            root.GetProperty("sourceSha256").GetString() == "15e2c8ef57f4c5f599589b5d15021281a11b73757be54d0bbf739d57dd280b99", "DUMP_SOURCE");
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
                int optical = c == 'ł' ? Math.Max(1, (int)Math.Floor(em / 32.0 + 0.5)) : 0;
                int shift = basis.Offset + optical - result.Glyph.Offset;
                for (int y = 0; y < basis.Image.H; y++) for (int x = 0; x < basis.Image.W; x++) if (basis.Image.Ink(x, y))
                    for (int channel = 0; channel < 4; channel++) FontAtlasCore.Need(basis.Image.Pixels[(y * basis.Image.W + x) * 4 + channel] == result.Glyph.Image.Pixels[(y * result.Glyph.Image.W + x + shift) * 4 + channel], "DUMP_BASE_CHANGED");
                if (review) Console.WriteLine("DUMP_REVIEW " + JsonSerializer.Serialize(new { name, character = c.ToString(), width = result.Glyph.Image.W, height = result.Glyph.Image.H, rgba = result.Glyph.Image.Pixels, provenance = result.Provenance }));
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
                    FontAtlasCore.Need(crop.W == result.Glyph.Image.W && crop.H == result.Glyph.Image.H && crop.Pixels.SequenceEqual(result.Glyph.Image.Pixels), "BASELINE_BITMAP_CHANGED");
                    FontAtlasCore.Need(result.Glyph.Offset == old.GetProperty("offset").GetInt32() + optical, "BASELINE_OFFSET");
                    FontAtlasCore.Need(result.Glyph.Shift == (c == 'ł' ? basis.Shift + optical : old.GetProperty("shift").GetInt32()), "BASELINE_SHIFT");
                    var oldReport = FontBuilderCore.Parse(File.ReadAllBytes(Path.Combine(baselineBundle, "report.json")));
                    var oldProvenance = oldReport.GetProperty("provenance").EnumerateArray().Single(x => x.GetProperty("name").GetString() == name)
                        .GetProperty("additions").EnumerateArray().Single(x => x.GetProperty("character").GetString() == c.ToString());
                    var current = JsonSerializer.SerializeToElement(result.Provenance);
                    foreach (string key in new[] { "geometry", "donorTransform", "markSha256", "resultSha256", "baseX", "baseY" })
                        FontAtlasCore.Need(JsonSerializer.Serialize(oldProvenance.GetProperty(key)) == JsonSerializer.Serialize(current.GetProperty(key)), "BASELINE_GEOMETRY " + key);
                }
            });
        }
    }
}
