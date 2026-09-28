#r "System.Text.Json"
#load "font_builder_core.cs"
#load "font_builder_io.cs"

using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text.Json;
using UndertaleModLib;
using UndertaleModLib.Models;

// Only this exact noninteractive host invocation is accepted. In particular, CLI -o
// has its own unsafe save path after scripts finish; CanSave does not guard it.
try
{
var argv = Environment.GetCommandLineArgs();
FontBuilderCore.Invocation(argv, ScriptPath);

var requestPath = FontBuilderCore.Absolute(Environment.GetEnvironmentVariable("CC_FONT_REQUEST"));
var requestBytes = FontBuilderIO.Read(requestPath, 1024 * 1024);
var request = FontBuilderCore.Request(requestBytes);
var source = FontBuilderCore.Text(request, "source");
FontBuilderCore.Require(source.Equals(FilePath, StringComparison.OrdinalIgnoreCase) &&
    Path.GetFullPath(argv[2]).Equals(source, StringComparison.OrdinalIgnoreCase) &&
    Path.GetFileName(source).Equals("data.win", StringComparison.OrdinalIgnoreCase), "CLI/request source mismatch");

// Separate installation identity: a staged source/request cannot override it.
var gameRoot = FontBuilderCore.Absolute(Environment.GetEnvironmentVariable("CC_GAME_ROOT"));
var sourceRoot = Path.GetDirectoryName(source);
var release = FontBuilderCore.Text(request, "releaseRoot");
var output = FontBuilderCore.Text(request, "output");
FontBuilderIO.CheckOutput(release, output, gameRoot, sourceRoot);

var libAssembly = typeof(UndertaleData).Assembly;
var cliAssembly = AppDomain.CurrentDomain.GetAssemblies().Single(a => a.GetName().Name == "UndertaleModCli");
FontBuilderCore.Require(cliAssembly.GetName().Version.ToString() == "0.9.2.0", "Unreviewed CLI version");
FontBuilderCore.Require(libAssembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion ==
    FontBuilderCore.LocalLibraryInfo, "Unreviewed UndertaleModLib revision");
var libBytes = FontBuilderIO.Read(libAssembly.Location, 64 * 1024 * 1024);
var cliBytes = FontBuilderIO.Read(cliAssembly.Location, 64 * 1024 * 1024);

// Ignore the host's already-loaded Data: it predates our expected-hash check.
// Parse exactly the verified snapshot, not a reopened path. Any UMT warning is fatal.
var sourceBytes = FontBuilderIO.Read(source, 1024 * 1024 * 1024);
FontBuilderCore.CheckHash(sourceBytes, FontBuilderCore.Text(request, "expectedSha256"));
using (var sourceStream = new MemoryStream(sourceBytes, false))
using (var verified = UndertaleIO.Read(sourceStream, (warning, important) => { throw new InvalidDataException("UMT warning: " + warning); }))
{
FontBuilderCore.Require(verified.Fonts != null && verified.TexturePageItems != null && verified.EmbeddedTextures != null,
    "Missing FONT/TPAG/TXTR");

var selection = FontBuilderCore.Selection(request);
var existing = new Dictionary<string, int[]>(StringComparer.Ordinal);
var snapshots = new List<object>();
foreach (var name in selection)
{
    var matches = verified.Fonts.Where(f => f != null && f.Name?.Content == name).ToArray();
    FontBuilderCore.Require(matches.Length == 1, "Missing/duplicate font resource: " + name);
    var f = matches[0];
    FontBuilderCore.Require(f.Glyphs != null && f.Glyphs.Count <= 65536 && f.Glyphs.All(g => g != null && g.Kerning != null), "Invalid base glyphs");
    var t = f.Texture;
    FontBuilderCore.Require(t != null && verified.TexturePageItems.Contains(t) && t.TexturePage != null &&
        verified.EmbeddedTextures.Contains(t.TexturePage), "Unregistered font texture");
    FontBuilderCore.Require(!t.TexturePage.TextureExternal && f.SDFSpread == 0, "External/SDF font requires another adapter");
    FontBuilderCore.Require(float.IsFinite(f.EmSize) && float.IsFinite(f.ScaleX) && float.IsFinite(f.ScaleY), "Nonfinite font metrics");
    existing.Add(name, f.Glyphs.Select(g => (int)g.Character).ToArray());

    // Snapshot explicitly, without Glyph.Clone (which omits UnknownAlwaysZero in the pinned source).
    var snapshot = new {
        name, displayName = f.DisplayName?.Content, f.EmSizeIsFloat, f.EmSize, f.Bold, f.Italic,
        f.RangeStart, f.RangeEnd, f.Charset, f.AntiAliasing, f.ScaleX, f.ScaleY,
        f.AscenderOffset, f.Ascender, f.SDFSpread, f.LineHeight,
        fontIndex = verified.Fonts.IndexOf(f), textureIndex = verified.TexturePageItems.IndexOf(t),
        pageIndex = verified.EmbeddedTextures.IndexOf(t.TexturePage),
        texture = new { t.SourceX, t.SourceY, t.SourceWidth, t.SourceHeight, t.TargetX, t.TargetY,
            t.TargetWidth, t.TargetHeight, t.BoundingWidth, t.BoundingHeight },
        glyphs = f.Glyphs.Select(g => new { g.Character, g.SourceX, g.SourceY, g.SourceWidth, g.SourceHeight,
            g.Shift, g.Offset, g.UnknownAlwaysZero,
            kerning = g.Kerning.Select(k => new { k.Character, k.ShiftModifier }).ToArray() }).ToArray()
    };
    snapshots.Add(new { name, snapshotSha256 = FontBuilderCore.Hash(JsonSerializer.SerializeToUtf8Bytes(snapshot)),
        glyphCount = f.Glyphs.Count, kerningCount = f.Glyphs.Sum(g => (long)g.Kerning.Count),
        pageIndex = snapshot.pageIndex, textureIndex = snapshot.textureIndex,
        pageItemUsers = verified.TexturePageItems.Count(p => p != null && ReferenceEquals(p.TexturePage, t.TexturePage)) });
}

var manifestPath = FontBuilderCore.Text(request, "manifest");
var manifestBytes = FontBuilderIO.Read(manifestPath, 1024 * 1024);
var manifest = FontBuilderCore.Parse(manifestBytes);
var atlases = new Dictionary<string, byte[]>(StringComparer.OrdinalIgnoreCase);
foreach (var f in FontBuilderCore.ManifestFonts(manifest))
{
    string file = FontBuilderCore.Leaf(FontBuilderCore.Text(f.GetProperty("atlas"), "file"));
    FontBuilderCore.Require(!atlases.ContainsKey(file), "Duplicate atlas file");
    atlases.Add(file, FontBuilderIO.Read(Path.Combine(Path.GetDirectoryName(manifestPath), file), 64 * 1024 * 1024));
}
var plan = FontBuilderCore.ValidateManifest(manifest, selection, request.GetProperty("allowFontES").GetBoolean(), existing, atlases);
var report = new {
    schema = "cc-font-preflight/v1", status = "blocked", preflightValid = true,
    mode = FontBuilderCore.Text(request, "mode"), buildEnabled = false, installed = false,
    required = FontBuilderCore.Required, source, sourceSha256 = FontBuilderCore.Hash(sourceBytes), sourceBytes = sourceBytes.Length,
    requestSha256 = FontBuilderCore.Hash(requestBytes), manifestSha256 = FontBuilderCore.Hash(manifestBytes),
    tool = new { cliVersion = cliAssembly.GetName().Version.ToString(), cliSha256 = FontBuilderCore.Hash(cliBytes),
        libVersion = libAssembly.GetName().Version.ToString(), libSha256 = FontBuilderCore.Hash(libBytes),
        libInformationalVersion = libAssembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion,
        inspectedSourceCommit = "f43e12c445c37d50dc6244caa12ccab232983f3f",
        binaryMatchesInspectedSource = false },
    oldSnapshots = snapshots, plannedChanges = plan, appliedChanges = new object[0],
    blockers = new[] { FontBuilderCore.Blocker, "LOCAL_DLL_SOURCE_REVISION_DIFFERS_FROM_INSPECTED_CLONE" },
    dataOutput = (string)null, dataOutputSha256 = (string)null, dataRoundTripValidated = false,
    warning = "Structural preflight only; no texture placement, data serialization or runtime/visual validation."
};
var reportBytes = JsonSerializer.SerializeToUtf8Bytes(report, new JsonSerializerOptions { WriteIndented = true });
string reportHash = FontBuilderIO.PublishReport(release, output, reportBytes, gameRoot, sourceRoot);
ScriptMessage("REPORT_READBACK_OK sha256=" + reportHash + "; BUILD_BLOCKED; appliedChanges=0");
// Both modes are deliberately nonzero: a valid preflight is not a successful font build.
FontBuilderCore.BuildGate();
}
}
catch (Exception error)
{
    Console.Error.WriteLine("FONT_BUILDER_REFUSED: " + error.Message);
    // Standalone CLI only. Never return control to a host save path, including on
    // invalid invocation with -o. Do not depend on differing CLI revisions' error handling.
    Environment.Exit(2);
}
