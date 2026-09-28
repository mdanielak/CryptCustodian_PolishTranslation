using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.Json;
using UndertaleModLib;
using UndertaleModLib.Models;
using UndertaleModLib.Util;

// Read-only UMT boundary. Never assigns to a model, never calls any serializer/importer.
public static class FontAtlasUmt
{
    // Value-only snapshot: diagnostics must never touch TextureData or load an image.
    public sealed class TextureDiagnostic
    {
        public string font { get; }
        public int fontIndex { get; }
        public int tpagIndex { get; }
        public int txtrIndex { get; }
        public bool TextureExternal { get; }
        public bool TextureLoaded { get; }
        // Raw, uncertain flag: provenance only, never a scale factor or an admission gate.
        public uint Scaled { get; }
        public uint GeneratedMips { get; }
        public string[] reasons => new[] {
            TextureExternal ? "TEXTURE_EXTERNAL: TextureExternal=true; required false" : null,
            !TextureLoaded ? "TEXTURE_NOT_LOADED: TextureLoaded=false; required true" : null,
            GeneratedMips != 0 ? "TEXTURE_GENERATED_MIPS: GeneratedMips=" + GeneratedMips + "; required 0" : null
        }.Where(r => r != null).ToArray();

        public TextureDiagnostic(string name, int fontId, int tpagId, int txtrId,
            bool external, bool loaded, uint scaled, uint mips)
        {
            font = name; fontIndex = fontId; tpagIndex = tpagId; txtrIndex = txtrId;
            TextureExternal = external; TextureLoaded = loaded; Scaled = scaled; GeneratedMips = mips;
        }

        public void RequireAllowed()
        {
            // Collect ALL failing reasons. Scaled says nothing about safe pixel geometry.
            FontBuilderCore.Require(!TextureExternal && TextureLoaded && GeneratedMips == 0,
                string.Join("; ", reasons));
        }
    }

    public static void RequireIdentityLayout(UndertaleTexturePageItem t)
    {
        // Independent of TXTR.Scaled; never resize or infer a multiplier from that flag.
        FontBuilderCore.Require(t != null && t.TargetX == 0 && t.TargetY == 0 &&
            t.SourceWidth == t.TargetWidth && t.SourceHeight == t.TargetHeight &&
            t.BoundingWidth == t.SourceWidth && t.BoundingHeight == t.SourceHeight, "TPAG_NONIDENTITY_LAYOUT");
    }

    static byte[] Json(object value) => JsonSerializer.SerializeToUtf8Bytes(value, new JsonSerializerOptions { WriteIndented = true });
    static object GlyphRecord(UndertaleFont.Glyph g) => new { g.Character, g.SourceX, g.SourceY, g.SourceWidth, g.SourceHeight,
        g.Shift, g.Offset, g.UnknownAlwaysZero, kerning = g.Kerning.Select(k => new { k.Character, k.ShiftModifier }).ToArray() };

    public static int Run(string[] argv, string scriptPath, string hostFilePath)
    {
        FontBuilderCore.Invocation(argv, scriptPath);
        byte[] requestBytes = FontBuilderIO.Read(FontBuilderCore.Absolute(Environment.GetEnvironmentVariable("CC_FONT_ATLAS_REQUEST")), 1024 * 1024);
        var request = FontBuilderCore.Parse(requestBytes);
        FontBuilderCore.Fields(request, "schema", "source", "expectedSha256", "outputDirectory");
        FontBuilderCore.Require(FontBuilderCore.Text(request, "schema") == "cc-font-atlas-request/v1", "Atlas request schema");
        string source = FontBuilderCore.Absolute(FontBuilderCore.Text(request, "source"));
        string expected = FontBuilderCore.Text(request, "expectedSha256");
        FontBuilderCore.Require(System.Text.RegularExpressions.Regex.IsMatch(expected, "\\A[0-9a-fA-F]{64}\\z"), "Expected SHA-256 required");
        FontBuilderCore.Require(source.Equals(hostFilePath, StringComparison.OrdinalIgnoreCase) && source.Equals(argv[2], StringComparison.OrdinalIgnoreCase) &&
            Path.GetFileName(source).Equals("data.win", StringComparison.OrdinalIgnoreCase), "CLI/request source mismatch");
        string gameRoot = FontBuilderCore.Absolute(Environment.GetEnvironmentVariable("CC_GAME_ROOT"));
        string sourceRoot = Path.GetDirectoryName(source), output = FontBuilderCore.Text(request, "outputDirectory");
        FontBuilderIO.CheckNewOutputDirectory(output, gameRoot, sourceRoot);
        var lib = typeof(UndertaleData).Assembly;
        var cli = AppDomain.CurrentDomain.GetAssemblies().Single(a => a.GetName().Name == "UndertaleModCli");
        FontBuilderCore.Require(cli.GetName().Version.ToString() == "0.9.2.0" &&
            lib.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion == FontBuilderCore.LocalLibraryInfo, "Unreviewed UMT version");
        var tool = new { cliVersion = cli.GetName().Version.ToString(), libVersion = FontBuilderCore.LocalLibraryInfo,
            cliSha256 = FontBuilderCore.Hash(FontBuilderIO.Read(cli.Location, 64 * 1024 * 1024)),
            libSha256 = FontBuilderCore.Hash(FontBuilderIO.Read(lib.Location, 64 * 1024 * 1024)),
            inspectedSourceCommit = "f43e12c445c37d50dc6244caa12ccab232983f3f", binaryMatchesInspectedSource = false };
        // Hash precedes this parse, not the CLI's unavoidable initial parse. Ignore host Data.
        byte[] snapshot = FontBuilderIO.Read(source, 1024 * 1024 * 1024);
        FontBuilderCore.CheckHash(snapshot, expected);
        var diagnostics = new List<object>(); var provenance = new List<object>();
        var textureDiagnostics = new List<TextureDiagnostic>();
        var manifests = new List<JsonElement>(); var atlases = new Dictionary<string, byte[]>();
        var existing = new Dictionary<string, int[]>(); bool blocked = false;
        try
        {
            using (var stream = new MemoryStream(snapshot, false))
            using (var data = UndertaleIO.Read(stream, (warning, important) => { throw new InvalidDataException("UMT_WARNING: " + warning); }))
            {
                foreach (string name in FontAtlasCore.Names)
                {
                    try
                    {
                        FontBuilderCore.Require(data.Fonts != null && data.TexturePageItems != null && data.EmbeddedTextures != null, "MISSING_FONT_TPAG_TXTR");
                        var matches = data.Fonts.Where(f => f != null && f.Name?.Content == name).ToArray();
                        FontBuilderCore.Require(matches.Length == 1, "MISSING_DUPLICATE_FONT");
                        var f = matches[0]; var t = f.Texture;
                        FontBuilderCore.Require(f.Glyphs != null && f.Glyphs.Count > 0 && f.Glyphs.Count <= 65536 &&
                            f.Glyphs.All(g => g != null && g.Kerning != null && g.UnknownAlwaysZero == 0 && !(g.Character >= 0xd800 && g.Character <= 0xdfff)) &&
                            f.Glyphs.Select(g => g.Character).Distinct().Count() == f.Glyphs.Count, "INVALID_GLYPH_MODEL");
                        FontBuilderCore.Require(f.SDFSpread == 0 && f.ScaleX == 1 && f.ScaleY == 1 && float.IsFinite(f.EmSize), "SDF_SCALE_METRICS_UNSUPPORTED");
                        FontBuilderCore.Require(t != null && data.TexturePageItems.Contains(t) && t.TexturePage != null && data.EmbeddedTextures.Contains(t.TexturePage), "UNREGISTERED_TEXTURE");
                        var page = t.TexturePage;
                        // Check before TextureData getter: it can otherwise load external files/placeholders.
                        var textureDiagnostic = new TextureDiagnostic(name, data.Fonts.IndexOf(f), data.TexturePageItems.IndexOf(t),
                            data.EmbeddedTextures.IndexOf(page), page.TextureExternal, page.TextureLoaded, page.Scaled, page.GeneratedMips);
                        textureDiagnostics.Add(textureDiagnostic);
                        textureDiagnostic.RequireAllowed();
                        RequireIdentityLayout(t);
                        var image = page.TextureData?.Image;
                        FontBuilderCore.Require(image != null && image.Width > 0 && image.Width <= 4096 && image.Height > 0 && image.Height <= 4096, "PAGE_DIMENSIONS");
                        byte[] bgra;
                        if (image.Format == GMImage.ImageFormat.Bz2Qoi)
                        {
                            FontBuilderCore.Require(data.IsVersionAtLeast(2022, 9), "BZ2QOI_TXTR_DIMENSIONS_UNAVAILABLE");
                            bgra = FontAtlasBz2Qoi.Decode(image, t, page.TextureWidth, page.TextureHeight, data.IsVersionAtLeast(2022, 5));
                        }
                        else
                        {
                            // Existing paths unchanged. DDS/unknown deliberately refused.
                            FontBuilderCore.Require(image.Format == GMImage.ImageFormat.Png || image.Format == GMImage.ImageFormat.Qoi || image.Format == GMImage.ImageFormat.RawBgra,
                                "UNBOUNDED_OR_UNSUPPORTED_IMAGE_FORMAT: format=" + image.Format);
                            var raw = image.ConvertToRawBgra();
                            FontBuilderCore.Require(raw.Format == GMImage.ImageFormat.RawBgra && raw.Width == image.Width && raw.Height == image.Height, "DECODE_DIMENSIONS");
                            bgra = raw.GetRawImageData().ToArray(); // NEVER modify the Span owned by GMImage.
                        }
                        FontBuilderCore.Require(bgra.Length == checked(image.Width * image.Height * 4), "DECODE_BYTE_LENGTH");
                        var pageBitmap = FontAtlasCore.FromBgra(image.Width, image.Height, bgra);
                        var fontBitmap = FontAtlasCore.Crop(pageBitmap, t.SourceX, t.SourceY, t.SourceWidth, t.SourceHeight);
                        var glyphModels = f.Glyphs.ToDictionary(g => (char)g.Character);
                        var extracted = new Dictionary<char, FontAtlasCore.Glyph>();
                        Func<char, FontAtlasCore.Glyph> get = c => {
                            if (extracted.TryGetValue(c, out var cached)) return cached;
                            FontBuilderCore.Require(glyphModels.ContainsKey(c), "DONOR_OR_BASE_MISSING U+" + ((int)c).ToString("X4"));
                            var g = glyphModels[c];
                            FontBuilderCore.Require(g.SourceWidth <= 512 && g.SourceHeight <= 512, "GLYPH_DIMENSIONS");
                            var value = new FontAtlasCore.Glyph { Character = c, Shift = g.Shift, Offset = g.Offset,
                                Image = FontAtlasCore.Crop(fontBitmap, g.SourceX, g.SourceY, g.SourceWidth, g.SourceHeight),
                                Kerning = JsonSerializer.SerializeToElement(g.Kerning.Select(k => new { character = (int)k.Character, shiftModifier = (int)k.ShiftModifier }).ToArray()) };
                            FontAtlasCore.Metrics(value); extracted.Add(c, value); return value;
                        };
                        int maxHeight = f.Glyphs.Max(g => (int)g.SourceHeight);
                        FontBuilderCore.Require(maxHeight <= 512, "FONT_HEIGHT_ENVELOPE");
                        existing.Add(name, f.Glyphs.Select(g => (int)g.Character).ToArray());
                        var additions = new List<FontAtlasCore.Composed>(); bool fontBlocked = false;
                        foreach (char c in FontBuilderCore.Required)
                        {
                            try
                            {
                                if (glyphModels.ContainsKey(c))
                                {
                                    get(c); diagnostics.Add(new { font = name, character = c.ToString(), status = "preserved" }); continue;
                                }
                                var basis = get(FontAtlasCore.Base(c));
                                FontAtlasCore.Bitmap mark; string donor; object acuteDiagnostic = null;
                                if ("ćńśźĆŃŚŹ".Contains(c))
                                {
                                    char acute = char.IsUpper(c) ? 'Ó' : 'ó', plain = char.IsUpper(c) ? 'O' : 'o';
                                    mark = FontAtlasCore.Acute(get(acute), get(plain), out acuteDiagnostic); donor = acute.ToString();
                                }
                                else if ("ąęĄĘłŁ".Contains(c))
                                {
                                    donor = "ąęĄĘ".Contains(c) ? "," : "/";
                                    mark = get(donor[0]).Image; // full original RGBA crop; core records tight crop + transform
                                }
                                else
                                {
                                    // Deterministic fallback to i only when the real period is absent/unusable.
                                    try { mark = FontAtlasCore.Dot(get('.'), false); donor = "."; }
                                    catch (InvalidDataException periodError)
                                    {
                                        try { mark = FontAtlasCore.Dot(get('i'), true); donor = "i"; }
                                        catch (InvalidDataException iError) { throw new InvalidDataException("DOT_DONORS_REFUSED: period=" + periodError.Message + "; i=" + iError.Message); }
                                    }
                                }
                                var composed = FontAtlasCore.Compose(c, basis, mark, donor, maxHeight, f.EmSize);
                                additions.Add(composed);
                                diagnostics.Add(new { font = name, character = c.ToString(), status = "composed-offline-candidate", acute = acuteDiagnostic });
                            }
                            catch (Exception e) { fontBlocked = true; diagnostics.Add(new { font = name, character = c.ToString(), status = "refused", reason = e.Message }); }
                        }
                        provenance.Add(new { name, fontIndex = data.Fonts.IndexOf(f), tpagIndex = data.TexturePageItems.IndexOf(t), pageIndex = data.EmbeddedTextures.IndexOf(page),
                            Scaled = textureDiagnostic.Scaled,
                            pageFormat = image.Format.ToString(), pageWidth = image.Width, pageHeight = image.Height, pageRgbaSha256 = FontBuilderCore.Hash(pageBitmap.Pixels),
                            boundedBz2Qoi = image.Format == GMImage.ImageFormat.Bz2Qoi ? new { adapter = "bounded-bz2qoi/v1",
                                maxBytes = FontAtlasBz2Qoi.MaxBytes, librarySha256 = FontAtlasBz2Qoi.LibrarySha256, zipSha256 = FontAtlasBz2Qoi.ZipSha256,
                                page.TextureWidth, page.TextureHeight } : null,
                            tpag = new { t.SourceX, t.SourceY, t.SourceWidth, t.SourceHeight, t.TargetX, t.TargetY, t.TargetWidth, t.TargetHeight, t.BoundingWidth, t.BoundingHeight },
                            metrics = new { f.EmSize, f.ScaleX, f.ScaleY, f.Ascender, f.AscenderOffset, f.LineHeight, maxHeight },
                            oldGlyphModelSha256 = FontBuilderCore.Hash(Json(f.Glyphs.Select(GlyphRecord).ToArray())),
                            donorsAndBases = extracted.OrderBy(k => k.Key).Select(k => new { character = k.Key.ToString(), model = GlyphRecord(glyphModels[k.Key]),
                                rgbaSha256 = FontBuilderCore.Hash(k.Value.Image.Pixels) }).ToArray(),
                            additions = additions.Select(a => a.Provenance).ToArray() });
                        if (!fontBlocked)
                        {
                            var packed = FontAtlasCore.Pack(name, additions); manifests.Add(packed.font); atlases.Add(name + ".rgba", packed.image.Pixels);
                        }
                        blocked |= fontBlocked;
                    }
                    catch (Exception e)
                    {
                        blocked = true;
                        foreach (char c in FontBuilderCore.Required) diagnostics.Add(new { font = name, character = c.ToString(), status = "refused-font", reason = e.Message });
                    }
                }
            }
        }
        catch (Exception e)
        {
            blocked = true;
            foreach (string name in FontAtlasCore.Names) foreach (char c in FontBuilderCore.Required)
                diagnostics.Add(new { font = name, character = c.ToString(), status = "refused-source", reason = e.Message });
        }
        var files = new Dictionary<string, byte[]>();
        if (!blocked)
        {
            var manifest = JsonSerializer.SerializeToElement(new { schema = "cc-font-atlas/v1", required = FontBuilderCore.Required, fonts = manifests });
            try
            {
                // Same manifest contract as the existing preflight, including coverage/kerning/no overlap.
                FontBuilderCore.ValidateManifest(manifest, FontAtlasCore.Names, false, existing, atlases);
            }
            catch (Exception e)
            {
                blocked = true;
                foreach (string name in FontAtlasCore.Names) foreach (char c in FontBuilderCore.Required)
                    diagnostics.Add(new { font = name, character = c.ToString(), status = "refused-manifest", reason = e.Message });
            }
            if (!blocked)
            {
                foreach (var a in atlases) files.Add(a.Key, a.Value);
                files.Add("manifest.json", Json(manifest));
            }
        }
        files.Add("report.json", Json(new { schema = "cc-font-atlas-report/v1", status = blocked ? "blocked" : "candidate",
            recipe = FontAtlasCore.Recipe, source, sourceSha256 = FontBuilderCore.Hash(snapshot), sourceBytes = snapshot.Length,
            requestSha256 = FontBuilderCore.Hash(requestBytes), tool, diagnostics, textureDiagnostics, provenance,
            outputs = files.OrderBy(f => f.Key, StringComparer.Ordinal).Select(f => new { file = f.Key, bytes = f.Value.Length, sha256 = FontBuilderCore.Hash(f.Value) }).ToArray(),
            dataOutput = (string)null, appliedChanges = new object[0], buildEnabled = false, installed = false,
            dataRoundTripValidated = false, runtimeValidated = false, visualValidated = false,
            warning = "Candidate pixels only. Baseline row origin preserved; no runtime baseline/line clipping claim. Writer remains blocked." }));
        string reportHash = FontAtlasIO.Publish(output, files, gameRoot, sourceRoot);
        Console.WriteLine("ATLAS_BUNDLE_READBACK_OK reportSha256=" + reportHash + "; status=" + (blocked ? "blocked" : "candidate"));
        return blocked ? 2 : 0;
    }
}
