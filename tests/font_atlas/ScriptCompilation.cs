using System;
using System.IO;
using System.Linq;
using System.Runtime.CompilerServices;
using System.Text;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp.Scripting;
using UndertaleModLib.Scripting;
using UndertaleModLib.Util;

internal static class ScriptCompilation
{
    public static void Run([CallerFilePath] string testFile = "")
    {
        string root = Path.GetFullPath(Path.Combine(Path.GetDirectoryName(testFile), "..", ".."));
        string script = Path.Combine(root, "umt_font_atlas.csx");
        var options = ScriptingUtil.CreateDefaultScriptOptions()
            .AddReferences(typeof(UndertaleModCli.Program).Assembly, typeof(Newtonsoft.Json.JsonConvert).Assembly)
            .WithFilePath(script).WithFileEncoding(Encoding.UTF8);
        // Compile only; do not create/run a script delegate or invoke any entry point.
        var errors = CSharpScript.Create(File.ReadAllText(script), options, typeof(IScriptInterface)).Compile()
            .Where(d => d.Severity == DiagnosticSeverity.Error).ToArray();
        if (errors.Length != 0) throw new Exception(string.Join(Environment.NewLine, errors.Select(e => e.ToString())));
    }
}
