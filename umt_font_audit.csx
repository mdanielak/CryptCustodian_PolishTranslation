using System;
using System.Collections.Generic;
using System.Text;
using UndertaleModLib.Models;

EnsureDataLoaded();

var wanted = "ąćęłńóśźżĄĆĘŁŃÓŚŹŻ";
var fonts = Data?.Fonts;
var report = new StringBuilder();
report.AppendLine("UMT_FONT_AUDIT_V1");

if (fonts is null)
{
    report.AppendLine("FONT_COUNT=<null>");
}
else
{
    report.AppendLine($"FONT_COUNT={fonts.Count}");
    for (int i = 0; i < fonts.Count; i++)
    {
        UndertaleFont font = fonts[i];
        if (font is null)
        {
            report.AppendLine($"FONT[{i}]=<null>");
            continue;
        }

        var present = new StringBuilder();
        var missing = new StringBuilder();
        var seen = new HashSet<ushort>();
        if (font.Glyphs is not null)
        {
            foreach (var glyph in font.Glyphs)
                if (glyph is not null)
                    seen.Add(glyph.Character);
        }

        foreach (char c in wanted)
        {
            if (seen.Contains((ushort)c))
                present.Append(c);
            else
                missing.Append(c);
        }

        report.AppendLine(
            $"FONT[{i}] name={font.Name?.Content ?? "<null>"}; " +
            $"display={font.DisplayName?.Content ?? "<null>"}; " +
            $"range={font.RangeStart}-{font.RangeEnd}; glyphs={font.Glyphs?.Count ?? 0}; " +
            $"present={present}; missing={missing}; complete={missing.Length == 0}");
    }
}

ScriptMessage(report.ToString());
