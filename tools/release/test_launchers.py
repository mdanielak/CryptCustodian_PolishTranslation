"""Exercise real cmd/BAT/Windows PS5.1 using harmless probes, never game files."""
import json
import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

from build_release import bat_bytes, text_bytes, write_new
from verify_sources import TEMP


PACKAGE = Path(os.environ["CC_RELEASE_PACKAGE"]).absolute() if os.environ.get("CC_RELEASE_PACKAGE") else None
ROOT = TEMP / ("ccpl-launcher-tests-" + uuid.uuid4().hex)
POLISH = "".join(chr(codepoint) for codepoint in (
    0x0105, 0x0107, 0x0119, 0x0142, 0x0144, 0x00F3, 0x015B, 0x017A, 0x017C,
    0x0020,
    0x0104, 0x0106, 0x0118, 0x0141, 0x0143, 0x00D3, 0x015A, 0x0179, 0x017B,
))
REQUIRED_LITERAL = "Wymagana jest lokalna, bezwzgl\u0119dna \u015bcie\u017cka z liter\u0105 dysku."


def ps_json(script, *args):
    response = ROOT / ("ps-json-" + uuid.uuid4().hex + ".json")
    environment = os.environ.copy()
    environment["CC_LAUNCHER_TEST_JSON"] = str(response)
    result = subprocess.run([
        "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
        str(script), *map(str, args),
    ], check=True, capture_output=True, env=environment)
    return json.loads(response.read_bytes().decode("utf-8"))


class LauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if PACKAGE is None:
            raise unittest.SkipTest("Pass package path or set CC_RELEASE_PACKAGE; harmless probes only")
        ROOT.mkdir(exist_ok=False)
        cls.policy = ROOT / "policy.ps1"
        write_new(cls.policy, text_bytes(r"""#requires -Version 5.1
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
$p = @{}
Get-ExecutionPolicy -List | ForEach-Object { $p[[string]$_.Scope] = [string]$_.ExecutionPolicy }
[IO.File]::WriteAllText($env:CC_LAUNCHER_TEST_JSON, ($p | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
""", bom=True))

    def test_bat_command_line_arguments_exit_and_process_policy_scope(self):
        assert PACKAGE is not None
        before = ps_json(self.policy)
        for action in ("instaluj", "odinstaluj"):
            with self.subTest(action=action):
                raw = (PACKAGE / (action + ".bat")).read_bytes()
                self.assertEqual(raw, bat_bytes(action))
                self.assertEqual(raw.decode("ascii").count("powershell.exe "), 1)
                self.assertIn(
                    f'powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0{action}.ps1" %*',
                    raw.decode("ascii"))
                self.assertNotIn(b"Set-ExecutionPolicy", raw)
                directory = ROOT / (action + " paczka " + "".join(map(chr, (0x7A, 0x61, 0x017C, 0x00F3, 0x0142, 0x0107))) + " & (test)!")
                directory.mkdir()
                bat = directory / (action + ".bat")
                write_new(bat, raw)  # exact shipped BAT, not a reimplementation
                probe = directory / (action + ".ps1")
                write_new(probe, text_bytes(r"""#requires -Version 5.1
[CmdletBinding()]
param([string]$GamePath, [switch]$NonInteractive, [string]$Action, [string]$Report)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -ErrorAction Stop
$policies = @{}
Get-ExecutionPolicy -List | ForEach-Object { $policies[[string]$_.Scope] = [string]$_.ExecutionPolicy }
$r = @{
    gamePath = $GamePath; nonInteractive = [bool]$NonInteractive; action = $Action
    script = $PSCommandPath; commandLine = [Environment]::CommandLine
    version = $PSVersionTable.PSVersion.ToString(); edition = $PSVersionTable.PSEdition
    policies = $policies; effectivePolicy = [string](Get-ExecutionPolicy)
    polish = -join ([int[]](261,263,281,322,324,243,347,378,380,32,260,262,280,321,323,211,346,377,379) | ForEach-Object { [char]$_ })
}
[IO.File]::WriteAllText($Report, ($r | ConvertTo-Json -Depth 4), [Text.UTF8Encoding]::new($false))
exit 37
""", bom=True))
                report = directory / "probe.json"
                # Quoted Unicode/spaces/metacharacters; delayed expansion is disabled.
                # No game exists at this path; the probe only records the supplied string.
                game = str(directory / ("Gra " + "".join(map(chr, (0x67, 0x0119, 0x015B, 0x6C, 0x0105))) + " & (" + "".join(map(chr, (0x017C, 0x00F3, 0x0142, 0x77))) + ")!"))
                command = (f'""{bat}" -GamePath "{game}" -NonInteractive '
                           f'-Action Preflight -Report "{report}""')
                result = subprocess.run(
                    f'"{os.environ["COMSPEC"]}" /d /v:off /s /c {command}',
                    cwd=ROOT, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 37, repr(result.stdout + result.stderr))
                r = json.loads(report.read_text(encoding="utf-8"))
                self.assertEqual(r["gamePath"], game)
                self.assertTrue(r["nonInteractive"])
                self.assertEqual(r["action"], "Preflight")
                self.assertEqual(r["script"], str(probe))
                self.assertTrue(r["version"].startswith("5.1."))
                self.assertEqual(r["edition"], "Desktop")
                self.assertEqual(r["polish"], POLISH)
                self.assertIn("-NoLogo -NoProfile -ExecutionPolicy Bypass -File", r["commandLine"])
                self.assertEqual(r["policies"]["Process"], "Bypass")
                for scope in ("MachinePolicy", "UserPolicy", "CurrentUser", "LocalMachine"):
                    self.assertEqual(r["policies"][scope], before[scope])
                if before["MachinePolicy"] == before["UserPolicy"] == "Undefined":
                    self.assertEqual(r["effectivePolicy"], "Bypass")
                self.assertEqual(ps_json(self.policy), before)

    def test_ps51_bom_parser_default_read_and_polish_codepoints(self):
        assert PACKAGE is not None
        for name in ("instaluj.ps1", "odinstaluj.ps1", "CZYTAJ_MNIE.txt", "LICENSE-NOTICE.txt"):
            raw = (PACKAGE / name).read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), name)
            self.assertNotIn("\ufeff", raw.decode("utf-8-sig"), name)
        for name in ("instaluj.ps1", "odinstaluj.ps1"):
            self.assertNotIn("Set-ExecutionPolicy", (PACKAGE / name).read_text(encoding="utf-8-sig"))
        manifest = (PACKAGE / "release-manifest.json").read_bytes()
        self.assertEqual(json.loads(manifest.decode("ascii"))["version"], "1.0-beta-rc1-C-Force")
        (PACKAGE / "SHA256SUMS.txt").read_bytes().decode("ascii")
        probe = ROOT / "bom-reader.ps1"
        write_new(probe, text_bytes("""#requires -Version 5.1
param([string]$Package)
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1) { throw 'Expected PS5.1' }
$result = @{}
foreach ($name in @('instaluj.ps1','odinstaluj.ps1','CZYTAJ_MNIE.txt','LICENSE-NOTICE.txt')) {
    $path = Join-Path $Package $name
    if ($name.EndsWith('.ps1')) {
        $tokens = $null; $errors = $null
        $ast = [Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
        if ($errors.Count) { throw ($errors | Out-String) }
    }
    $text = Get-Content -LiteralPath $path -Raw
    $result[$name] = @($text.ToCharArray() | ForEach-Object { [int]$_ })
    if ($name -eq 'instaluj.ps1') {
        $requiredLiteralPart = -join ([int[]](98,101,122,119,122,103,108,281,100,110,97) | ForEach-Object { [char]$_ })
        $literal = $ast.FindAll({param($a) $a -is [Management.Automation.Language.StringConstantExpressionAst] -and $a.Value -like ('*' + $requiredLiteralPart + '*')}, $true)
        if (-not $literal.Count) { throw 'Missing parsed Polish literal' }
        $result['literal'] = @($literal[0].Value.ToCharArray() | ForEach-Object { [int]$_ })
    }
}
[IO.File]::WriteAllText($env:CC_LAUNCHER_TEST_JSON, ($result | ConvertTo-Json -Depth 5 -Compress), [Text.UTF8Encoding]::new($false))
""", bom=True))
        r = ps_json(probe, PACKAGE)
        for name in ("instaluj.ps1", "odinstaluj.ps1", "CZYTAJ_MNIE.txt", "LICENSE-NOTICE.txt"):
            decoded = "".join(map(chr, r[name]))
            self.assertEqual(decoded, (PACKAGE / name).read_bytes().decode("utf-8-sig"))
        self.assertEqual(r["literal"], list(map(ord, REQUIRED_LITERAL)))


if __name__ == "__main__":
    if len(sys.argv) == 2:
        PACKAGE = Path(sys.argv[1]).absolute()
    elif len(sys.argv) != 1:
        raise SystemExit("Usage: test_launchers.py [package]")
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(LauncherTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    ROOT.mkdir(exist_ok=True)
    write_new(ROOT / "tests.json", json.dumps({
        "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "syntheticOnly": True, "package": str(PACKAGE),
    }, indent=2).encode("utf-8"))
    print("REPORT=" + str(ROOT / "tests.json"))
    raise SystemExit(not result.wasSuccessful())
