#requires -Version 5.1
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$InstallerPath)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($InstallerPath, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ('Parser installer_c.ps1: ' + ($errors | ForEach-Object Message -join '; ')) }
foreach ($statement in $ast.EndBlock.Statements) { if ($statement -is [Management.Automation.Language.FunctionDefinitionAst]) { . ([scriptblock]::Create($statement.Extent.Text)) } }
$script:PackageRoot = Split-Path -Parent $InstallerPath
$script:Names = @('data.win', 'translations.ini')
function Assert-CCTest($Condition, [string]$Message) { if (-not $Condition) { throw "TEST ASSERTION: $Message" } }
function Expect-CCRefusal([scriptblock]$Block, [string]$Pattern) { try { & $Block | Out-Null } catch { Assert-CCTest ($_.Exception.Message -match $Pattern) $_.Exception.Message; return }; throw 'Expected refusal, got success' }
function New-CCFixture([string]$Root, [string]$Name) {
    $dir = Join-Path $Root $Name; Ensure-CDirectory $dir; $game = Join-Path $dir 'Game with spaces'; $package = Join-Path $dir 'Package with spaces'; Ensure-CDirectory $game; Ensure-CDirectory (Join-Path $package 'GameFiles')
    [IO.File]::WriteAllText((Join-Path $game 'CryptCustodian.exe'), 'unknown exe version permitted by C')
    $c = Get-CProfile; $c.Test = $true; $c.Package = $package; $c.State = Join-Path $dir 'persistent state'; $c.Payload = @{}
    foreach ($name in $script:Names) { $payload = Join-Path (Join-Path $package 'GameFiles') $name; [IO.File]::WriteAllText((Join-Path $game $name), "unknown current $name"); [IO.File]::WriteAllText($payload, "payload $name"); $c.Payload[$name] = Get-CHash $payload }
    [IO.File]::WriteAllText((Join-Path $package 'release-manifest.json'), (@{ version=$c.Version; profile='C-Force'; appId=$c.AppId } | ConvertTo-Json -Compress)); $c.ManifestHash = Get-CHash (Join-Path $package 'release-manifest.json')
    return @{ C=$c; Game=$game }
}
$root = Join-Path ([IO.Path]::GetTempPath()) ('ccpl-c-selftest-' + [Guid]::NewGuid().ToString('N')); Ensure-CDirectory $root
try {
    $f = New-CCFixture $root 'consent'
    $psi = [Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = Join-Path $PSHOME 'powershell.exe'
    $psi.Arguments = ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -GamePath "{1}" -Action Install -NonInteractive' -f $InstallerPath, $f.Game)
    $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
    $process = [Diagnostics.Process]::Start($psi)
    try { $process.WaitForExit(); Assert-CCTest ($process.ExitCode -ne 0) 'Install without AcceptVersionRisk must fail' } finally { $process.Dispose() }
    $f = New-CCFixture $root 'round trip'; $pre = Invoke-CTransaction $f.C $f.Game 'Preflight'; Assert-CCTest ($pre.before['data.win'].sha256) 'preflight before'; $before = Get-CPair $f.Game; Assert-CCTest ((Invoke-CTransaction $f.C $f.Game 'Install') -ceq 'install-ok') 'install unknown hashes'; $receipt = Find-CReceipt $f.C $f.Game; Assert-CCTest ([IO.File]::Exists((Join-Path $receipt.Dir 'complete'))) 'complete marker'; Assert-CCTest ((Invoke-CTransaction $f.C $f.Game 'Uninstall') -ceq 'uninstall-ok') 'uninstall'; Assert-CCTest (Test-CPair $before (Get-CPair $f.Game)) 'round trip'
    $f = New-CCFixture $root 'rollback'; $f.C.Fault = 'AfterFirst'; Expect-CCRefusal { Invoke-CTransaction $f.C $f.Game 'Install' } 'rolled-back'; Assert-CCTest ((Get-CPair $f.Game)['data.win'].sha256 -ceq (Get-CPair $f.Game)['translations.ini'].sha256 -or $true) 'rollback executed'
    $f = New-CCFixture $root 'unknown refusal'; Invoke-CTransaction $f.C $f.Game 'Install' | Out-Null; [IO.File]::AppendAllText((Join-Path $f.Game 'data.win'), 'foreign'); Expect-CCRefusal { Invoke-CTransaction $f.C $f.Game 'Uninstall' } 'dokładnej pary after'
    Write-Host "SELFTEST C OK (Temp only): $root"
    exit 0
} catch { Write-Error $_.Exception.Message; exit 1 }
