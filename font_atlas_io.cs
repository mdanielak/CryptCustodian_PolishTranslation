using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;

public static class FontAtlasIO
{
    [DllImport("kernel32.dll", EntryPoint = "CreateDirectoryW", ExactSpelling = true, CharSet = CharSet.Unicode, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool CreateDirectoryExclusive(string path, IntPtr securityAttributes);

    // Directory reservation is exclusive, unlike Directory.CreateDirectory. No cleanup or overwrite.
    // Report is last. Partial files on I/O failure are NOT a completed bundle.
    public static string Publish(string output, Dictionary<string, byte[]> files, params string[] protectedRoots)
    {
        FontBuilderCore.Require(files.ContainsKey("report.json") && files.Count <= 6, "Bundle report/count");
        foreach (var f in files)
        {
            FontBuilderCore.Leaf(f.Key);
            FontBuilderCore.Require(f.Key == "report.json" || f.Key == "manifest.json" ||
                FontAtlasCore.Names.Any(n => f.Key == n + ".rgba"), "Unexpected output file");
            FontBuilderCore.Require(f.Value.Length <= 64 * 1024 * 1024, "Bundle file size");
            if (f.Key.EndsWith(".json", StringComparison.Ordinal)) FontBuilderCore.Parse(f.Value);
        }
        FontBuilderIO.CheckNewOutputDirectory(output, protectedRoots);
        FontBuilderCore.Require(CreateDirectoryExclusive(output, IntPtr.Zero), "Exclusive directory creation failed: " + Marshal.GetLastWin32Error());
        foreach (var f in files.OrderBy(f => f.Key == "report.json" ? 1 : 0).ThenBy(f => f.Key, StringComparer.Ordinal))
        {
            string path = Path.Combine(output, f.Key);
            FontBuilderIO.NoLinks(output); FontBuilderIO.NoLinks(path, true);
            using (var stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            { stream.Write(f.Value, 0, f.Value.Length); stream.Flush(true); }
            byte[] readback = FontBuilderIO.Read(path, 64 * 1024 * 1024);
            FontBuilderCore.Require(readback.SequenceEqual(f.Value), "Bundle readback mismatch: " + f.Key);
            if (f.Key.EndsWith(".json", StringComparison.Ordinal)) FontBuilderCore.Parse(readback);
        }
        return FontBuilderCore.Hash(files["report.json"]);
    }
}
