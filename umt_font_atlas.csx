#r "System.Text.Json"
#r "work/tools/UTMT_CLI_v0.9.2.0-Windows/ICSharpCode.SharpZipLib.dll"
#load "font_builder_core.cs"
#load "font_builder_io.cs"
#load "font_atlas_core.cs"
#load "font_atlas_io.cs"
#load "font_atlas_bz2qoi.cs"
#load "font_atlas_umt.cs"

// Dedicated standalone CLI process only; never return to a possible host save path.
try
{
    System.Environment.Exit(FontAtlasUmt.Run(System.Environment.GetCommandLineArgs(), ScriptPath, FilePath));
}
catch (System.Exception error)
{
    System.Console.Error.WriteLine("FONT_ATLAS_REFUSED: " + error.Message);
    System.Environment.Exit(2);
}
