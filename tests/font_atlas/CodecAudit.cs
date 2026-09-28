using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Reflection.Emit;
using UndertaleModLib.Util;
using ICSharpCode.SharpZipLib.BZip2;

// Inspection of the actual local binaries, not execution of their parsers.
internal static class CodecAudit
{
    static readonly Dictionary<ushort, OpCode> Codes = typeof(OpCodes).GetFields(BindingFlags.Public | BindingFlags.Static)
        .Where(f => f.FieldType == typeof(OpCode)).Select(f => (OpCode)f.GetValue(null)).ToDictionary(c => unchecked((ushort)c.Value));

    public static void Run(bool dump)
    {
        foreach (var a in new[] { typeof(GMImage).Assembly, typeof(BZip2).Assembly })
        {
            string hash = FontBuilderCore.Hash(File.ReadAllBytes(a.Location));
            FontBuilderCore.Require(hash == (a == typeof(GMImage).Assembly ? FontAtlasBz2Qoi.LibrarySha256 : FontAtlasBz2Qoi.ZipSha256), "Unreviewed codec DLL");
            Console.WriteLine("CODEC " + a.FullName + " info=" + a.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion +
                " mvid=" + a.ManifestModule.ModuleVersionId + " sha256=" + hash);
        }
        if (!dump) return;
        foreach (var t in new[] { typeof(GMImage), typeof(QoiConverter), typeof(BZip2), typeof(BZip2InputStream),
            typeof(UndertaleModLib.Models.UndertaleEmbeddedTexture) })
        {
            var names = new[] { "FromBinaryReader", "FindEndOfBZ2Stream", "FindEndOfBZ2Search", "FromBz2Qoi", "FromQoi",
                "GetInitialUncompressedBufferCapacity", "ConvertToFormat", "ConvertToRawBgra", "GetRawImageData", "ToSpan", "WriteToBinaryWriter",
                "GetImageFromStream", "GetImageFromSpan", "Decompress", "Read", "ReadByte", "Initialize", "SetDecompressStructureSizes",
                "InitBlock", "GetAndMoveToFrontDecode", "RecvDecodingTables", "SetupBlock", "BsR", "Complete", "EndBlock",
                "FillBuffer", "SetupRandPartA", "SetupRandPartB", "SetupRandPartC", "SetupNoRandPartA", "SetupNoRandPartB", "SetupNoRandPartC",
                "HbCreateDecodeTables", "Dispose", "CrcError", "BadBlockHeader", "BlockOverrun", "CompressedStreamEOF", "Unserialize" };
            var methods = t.GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static | BindingFlags.Instance | BindingFlags.DeclaredOnly)
                .Where(m => names.Contains(m.Name)).Cast<MethodBase>().Concat(t.GetConstructors(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance));
            foreach (var method in methods) Dump(method);
        }
    }

    static void Dump(MethodBase m)
    {
        var body = m.GetMethodBody(); if (body == null) return;
        byte[] il = body.GetILAsByteArray();
        Console.WriteLine("IL " + m.DeclaringType.FullName + "." + m + " initLocals=" + body.InitLocals + " sha256=" + FontBuilderCore.Hash(il));
        foreach (var local in body.LocalVariables) Console.WriteLine("  LOCAL " + local.LocalIndex + " " + local.LocalType);
        foreach (var clause in body.ExceptionHandlingClauses)
            Console.WriteLine("  EH " + clause.Flags + " try=" + clause.TryOffset.ToString("X4") + "+" + clause.TryLength +
                " handler=" + clause.HandlerOffset.ToString("X4") + "+" + clause.HandlerLength +
                (clause.Flags == ExceptionHandlingClauseOptions.Clause ? " catch=" + clause.CatchType : ""));
        int p = 0;
        while (p < il.Length)
        {
            int start = p; ushort code = il[p++]; if (code == 0xfe) code = (ushort)(0xfe00 | il[p++]);
            var op = Codes[code]; object arg = "";
            switch (op.OperandType)
            {
                case OperandType.InlineNone: break;
                case OperandType.ShortInlineI: arg = (sbyte)il[p++]; break;
                case OperandType.ShortInlineVar: arg = il[p++]; break;
                case OperandType.InlineVar: arg = BitConverter.ToUInt16(il, p); p += 2; break;
                case OperandType.InlineI: arg = BitConverter.ToInt32(il, p); p += 4; break;
                case OperandType.InlineI8: arg = BitConverter.ToInt64(il, p); p += 8; break;
                case OperandType.ShortInlineR: arg = BitConverter.ToSingle(il, p); p += 4; break;
                case OperandType.InlineR: arg = BitConverter.ToDouble(il, p); p += 8; break;
                case OperandType.ShortInlineBrTarget: int delta = (sbyte)il[p++]; arg = "IL_" + (p + delta).ToString("X4"); break;
                case OperandType.InlineBrTarget: int d = BitConverter.ToInt32(il, p); p += 4; arg = "IL_" + (p + d).ToString("X4"); break;
                case OperandType.InlineSwitch:
                    int n = BitConverter.ToInt32(il, p); p += 4; int end = p + 4 * n;
                    var targets = new List<string>(); for (int i = 0; i < n; i++) { targets.Add("IL_" + (end + BitConverter.ToInt32(il, p)).ToString("X4")); p += 4; }
                    arg = string.Join(",", targets); break;
                default:
                    int token = BitConverter.ToInt32(il, p); p += 4;
                    try {
                        if (op.OperandType == OperandType.InlineString) arg = m.Module.ResolveString(token);
                        else { var member = m.Module.ResolveMember(token); arg = member?.DeclaringType + "." + member; }
                    }
                    catch (ArgumentException) { arg = "token=" + token.ToString("X8"); }
                    break;
            }
            Console.WriteLine("  " + start.ToString("X4") + " " + op.Name + " " + arg);
        }
    }
}
