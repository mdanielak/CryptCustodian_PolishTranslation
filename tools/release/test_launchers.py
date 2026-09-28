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
POLISH = "ąćęłńóśźż ĄĆĘŁŃÓŚŹŻ"


def ps_json(script, *args):
    result = subprocess.run([
        "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
        str(script), *map(str, args),
    ], check=True, capture_output=True)
    return json.loads(result.stdout.decode("ascii"))


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
$p | ConvertTo-Json -Compress
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
                directory = ROOT / (action + " paczka zażółć & (test)!")
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
    polish = 'ąćęłńóśźż ĄĆĘŁŃÓŚŹŻ'
}
[IO.File]::WriteAllText($Report, ($r | ConvertTo-Json -Depth 4), [Text.UTF8Encoding]::new($false))
exit 37
""", bom=True))
                report = directory / "probe.json"
                # Quoted Unicode/spaces/metacharacters; delayed expansion is disabled.
                # No game exists at this path; the probe only records the supplied string.
                game = str(directory / "Gra gęślą & (żółw)!")
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
        self.assertEqual(json.loads(manifest.decode("ascii"))["version"], "1.0-beta")
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
        $literal = $ast.FindAll({param($a) $a -is [Management.Automation.Language.StringConstantExpressionAst] -and $a.Value -like '*bezwzględna*'}, $true)
        if (-not $literal.Count) { throw 'Missing parsed Polish literal' }
        $result['literal'] = @($literal[0].Value.ToCharArray() | ForEach-Object { [int]$_ })
    }
}
$result | ConvertTo-Json -Depth 5 -Compress
""", bom=True))
        r = ps_json(probe, PACKAGE)
        for name in ("instaluj.ps1", "odinstaluj.ps1", "CZYTAJ_MNIE.txt", "LICENSE-NOTICE.txt"):
            decoded = "".join(map(chr, r[name]))
            self.assertEqual(decoded, (PACKAGE / name).read_bytes().decode("utf-8-sig"))
        self.assertIn(POLISH, "".join(map(chr, r["instaluj.ps1"])))
        self.assertEqual("".join(map(chr, r["literal"])),
                         "Wymagana jest lokalna, bezwzględna ścieżka z literą dysku.")


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
