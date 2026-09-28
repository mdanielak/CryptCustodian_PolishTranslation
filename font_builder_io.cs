using System;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;

// Deliberately separate from pure validation. Never writes any game resource.
public static class FontBuilderIO
{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern uint QueryDosDevice(string name, StringBuilder target, int length);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern uint GetLongPathName(string path, StringBuilder expanded, int length);

    static string VolumePath(string path)
    {
        var expanded = new StringBuilder(32768);
        uint size = GetLongPathName(path, expanded, expanded.Capacity);
        FontBuilderCore.Require(size > 0 && size < expanded.Capacity, "Cannot expand existing path (8.3 alias check)");
        path = expanded.ToString();
        // Reject SUBST/network/unknown drive mappings and compare aliases of the
        // same local volume by device identity, not just their drive letters.
        var target = new StringBuilder(32768);
        FontBuilderCore.Require(QueryDosDevice(path.Substring(0, 2), target, target.Capacity) != 0, "Cannot resolve local volume");
        string device = target.ToString();
        FontBuilderCore.Require(System.Text.RegularExpressions.Regex.IsMatch(device, @"\A\\Device\\HarddiskVolume[0-9]+\z"),
            "SUBST/network/unknown volume mapping refused");
        return device + path.Substring(2);
    }

    public static void NoLinks(string path, bool leafMayBeAbsent = false)
    {
        FontBuilderCore.Absolute(path);
        string current = path;
        bool first = true;
        while (current != null)
        {
            try
            {
                var attr = File.GetAttributes(current);
                FontBuilderCore.Require((attr & FileAttributes.ReparsePoint) == 0, "Reparse point refused");
                if (!first) FontBuilderCore.Require((attr & FileAttributes.Directory) != 0, "Ancestor not a directory");
            }
            catch (FileNotFoundException) when (first && leafMayBeAbsent) { }
            catch (DirectoryNotFoundException) when (first && leafMayBeAbsent) { }
            first = false;
            current = Path.GetDirectoryName(current);
        }
    }

    public static byte[] Read(string path, int maxBytes)
    {
        NoLinks(path);
        using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            FontBuilderCore.Require(stream.Length <= maxBytes, "Input exceeds size limit");
            var bytes = new byte[checked((int)stream.Length)];
            stream.ReadExactly(bytes);
            FontBuilderCore.Require(stream.ReadByte() == -1, "Input grew during read");
            return bytes;
        }
    }

    public static void CheckExternalDirectory(string directory, params string[] protectedRoots)
    {
        NoLinks(directory);
        FontBuilderCore.Require(Directory.Exists(directory), "Output root must already exist");
        string physical = VolumePath(directory);
        foreach (string root in protectedRoots)
        {
            NoLinks(root);
            FontBuilderCore.Require(!FontBuilderCore.Within(directory, root) &&
                !FontBuilderCore.Within(physical, VolumePath(root)), "Output aliases game/source installation");
        }
    }

    public static void CheckOutput(string release, string output, params string[] protectedRoots)
    {
        FontBuilderCore.OutputPolicy(release, output, protectedRoots);
        NoLinks(release); NoLinks(output, true);
        string physicalRelease = VolumePath(release);
        foreach (string root in protectedRoots)
        {
            NoLinks(root);
            FontBuilderCore.Require(!FontBuilderCore.Within(physicalRelease, VolumePath(root)), "Release aliases game/source installation");
        }
        FontBuilderCore.Require(Directory.Exists(release), "Release must already exist");
        FontBuilderCore.Require(!File.Exists(output) && !Directory.Exists(output), "Output already exists");
    }

    public static string PublishReport(string release, string output, byte[] bytes, params string[] protectedRoots)
    {
        // Parse before creating; no overwrite, temp rename, automatic cleanup or directory creation.
        FontBuilderCore.Parse(bytes);
        CheckOutput(release, output, protectedRoots);
        using (var stream = new FileStream(output, FileMode.CreateNew, FileAccess.Write, FileShare.None))
        {
            stream.Write(bytes, 0, bytes.Length);
            stream.Flush(true);
        }
        byte[] reread = Read(output, 1024 * 1024);
        FontBuilderCore.Require(bytes.SequenceEqual(reread), "Published report read-back mismatch");
        FontBuilderCore.Parse(reread);
        return FontBuilderCore.Hash(reread);
    }

    public static void CheckNewOutputDirectory(string output, params string[] protectedRoots)
    {
        FontBuilderCore.Absolute(output);
        FontBuilderCore.Leaf(Path.GetFileName(output));
        string parent = Path.GetDirectoryName(output);
        FontBuilderCore.Require(Path.GetFileName(parent).Equals("release", StringComparison.OrdinalIgnoreCase), "Parent must be named release");
        NoLinks(parent); NoLinks(output, true);
        FontBuilderCore.Require(Directory.Exists(parent), "Output parent must already exist");
        string physicalParent = VolumePath(parent);
        foreach (string root in protectedRoots)
        {
            NoLinks(root);
            FontBuilderCore.Require(!FontBuilderCore.Within(parent, root) &&
                !FontBuilderCore.Within(physicalParent, VolumePath(root)), "Output aliases game/source installation");
        }
        FontBuilderCore.Require(!File.Exists(output) && !Directory.Exists(output), "Output directory must be new (no-clobber)");
    }
}
