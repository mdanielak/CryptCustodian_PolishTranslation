#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$GamePath,
    [ValidateSet('Install', 'Uninstall', 'Preflight')][string]$Action = 'Install',
    [switch]$NonInteractive,
    [switch]$AcceptVersionRisk,
    [switch]$SelfTest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:PackageRoot = $PSScriptRoot
$script:Names = @('data.win', 'translations.ini')
$script:PackageFiles = @('CZYTAJ_MNIE.txt', 'GameFiles/data.win', 'GameFiles/translations.ini', 'INSTALUJ.sh', 'LICENSE-NOTICE.txt', 'ODINSTALUJ.sh', 'SHA256SUMS.txt', 'c_force_installer.py', 'instaluj.bat', 'instaluj.ps1', 'odinstaluj.bat', 'odinstaluj.ps1', 'release-manifest.json', 'selftest_c.ps1')

function Get-CFullPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or $Path -notmatch '^[a-zA-Z]:[\\/]') { throw 'Wymagana jest lokalna, bezwzględna ścieżka z literą dysku.' }
    if ($Path.Substring(2).Contains(':')) { throw 'Alternatywne strumienie danych (ADS) nie są obsługiwane.' }
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\\')
    foreach ($part in $full.Substring(3).Split('\\')) { if ($part.EndsWith('.') -or $part.EndsWith(' ')) { throw 'Ścieżka jest niejednoznaczna.' } }
    return $full
}
function Assert-CSafePath([string]$Path) {
    $p = Get-CFullPath $Path
    while ($p) {
        try { $attributes = [IO.File]::GetAttributes($p) }
        catch [IO.FileNotFoundException] { $attributes = 0 }
        catch [IO.DirectoryNotFoundException] { $attributes = 0 }
        if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Niedozwolone przekierowanie ścieżki (reparse point): $p" }
        $p = [IO.Path]::GetDirectoryName($p)
    }
}
function Get-CHash([string]$Path) {
    Assert-CSafePath $Path
    $stream = [IO.File]::Open($Path, 'Open', 'Read', 'Read')
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose(); $stream.Dispose() }
}
function Write-CNewBytes([string]$Path, [byte[]]$Bytes) {
    Assert-CSafePath $Path
    $stream = [IO.File]::Open($Path, 'CreateNew', 'Write', 'None')
    try { $stream.Write($Bytes, 0, $Bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
}
function Write-CNewJson([string]$Path, $Value) {
    Write-CNewBytes $Path ([Text.UTF8Encoding]::new($false).GetBytes(($Value | ConvertTo-Json -Depth 16 -Compress)))
}
function Ensure-CDirectory([string]$Path) { Assert-CSafePath $Path; [void][IO.Directory]::CreateDirectory($Path); Assert-CSafePath $Path }
function New-COwnedDirectory([string]$Parent, [string]$Name) {
    Ensure-CDirectory $Parent
    $dir = Join-Path $Parent $Name
    if ([IO.Directory]::Exists($dir) -or [IO.File]::Exists($dir)) { throw 'Katalog transakcji już istnieje.' }
    Write-CNewBytes ($dir + '.reservation') ([Text.Encoding]::UTF8.GetBytes($Name))
    [void][IO.Directory]::CreateDirectory($dir)
    Assert-CSafePath $dir
    Write-CNewBytes (Join-Path $dir '.owner') ([Text.Encoding]::UTF8.GetBytes($Name))
    return $dir
}
function Copy-CVerified([string]$Source, [string]$Destination, [string]$Expected) {
    if ((Get-CHash $Source) -cne $Expected) { throw "Niezgodny SHA-256 źródła: $Source" }
    Assert-CSafePath $Destination
    $input = [IO.File]::Open($Source, 'Open', 'Read', 'Read')
    try { $output = [IO.File]::Open($Destination, 'CreateNew', 'Write', 'None'); try { $input.CopyTo($output); $output.Flush($true) } finally { $output.Dispose() } } finally { $input.Dispose() }
    if ((Get-CHash $Destination) -cne $Expected) { throw 'Błąd weryfikacji kopii.' }
}
function Get-CProfile {
    return @{ Version = '1.0-beta-rc1-C-Force'; AppId = '2394650'; ManifestHash = '__MANIFEST_SHA256__'; Payload = @{
        'data.win' = '6ec2a376367df0832aea144f48e5b8928bc3e3cb466615bb97b2bdf51c477f1b';
        'translations.ini' = 'deeda976d7bf414bce0fe1af7d0f9719efa990bb83d4c956f989dfc886dc055f'
    }; Package = $script:PackageRoot; State = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'CryptCustodianPolishPatchC'); Test = $false; Fault = '' }
}
function Assert-CPackage($C) {
    Assert-CSafePath $C.Package
    $manifestPath = Join-Path $C.Package 'release-manifest.json'
    if ((Get-CHash $manifestPath) -cne $C.ManifestHash) { throw 'Manifest paczki niezgodny.' }
    $manifest = [IO.File]::ReadAllText($manifestPath) | ConvertFrom-Json
    if ($manifest.version -cne $C.Version -or $manifest.profile -cne 'C-Force' -or $manifest.appId -cne $C.AppId) { throw 'Niezgodny manifest C.' }
    if (-not $C.Test) {
        $found = @(Get-CFiles $C.Package '')
        if (@(Compare-Object ($script:PackageFiles | Sort-Object) ($found | Sort-Object) -CaseSensitive).Count -ne 0) { throw 'Paczka C ma niezgodn? zamkni?t? list? plik?w.' }
        $seen = @{}
        foreach ($line in [IO.File]::ReadAllLines((Join-Path $C.Package 'SHA256SUMS.txt'))) {
            if ($line -cnotmatch '^([0-9a-f]{64})  (.+)$') { throw 'Niepoprawna lista SHA256SUMS C.' }
            $hash = $Matches[1]; $name = $Matches[2]
            if ($name -ceq 'SHA256SUMS.txt' -or $script:PackageFiles -cnotcontains $name -or $seen.ContainsKey($name)) { throw 'Niepoprawna nazwa w SHA256SUMS C.' }
            $seen[$name] = $true
            if ((Get-CHash (Join-Path $C.Package $name)) -cne $hash) { throw 'Niezgodny SHA256SUMS C.' }
        }
        if ($seen.Count -ne ($script:PackageFiles.Count - 1)) { throw 'Niepe?na lista SHA256SUMS C.' }
    }
    foreach ($name in $script:Names) {
        $payload = Join-Path (Join-Path $C.Package 'GameFiles') $name
        if ((Get-CHash $payload) -cne $C.Payload[$name]) { throw "Payload paczki niezgodny: $name" }
    }
}
function Get-CFiles([string]$Root, [string]$Relative) {
    $path = if ($Relative) { Join-Path $Root $Relative } else { $Root }
    Assert-CSafePath $path
    foreach ($child in [IO.Directory]::EnumerateFileSystemEntries($path)) {
        Assert-CSafePath $child
        $name = [IO.Path]::GetFileName($child); $rel = if ($Relative) { $Relative + '/' + $name } else { $name }
        if ([IO.Directory]::Exists($child)) { Get-CFiles $Root $rel } else { $rel }
    }
}
function Assert-CTarget([string]$Game) {
    $game = Get-CFullPath $Game; Assert-CSafePath $game
    foreach ($name in @('CryptCustodian.exe') + $script:Names) { if (-not [IO.File]::Exists((Join-Path $game $name))) { throw "Brak wymaganego pliku: $name" } }
    return $game
}
function Get-CPair([string]$Game) {
    $pair = [ordered]@{}
    foreach ($name in $script:Names) { $path = Join-Path $Game $name; $pair[$name] = [ordered]@{ sha256 = Get-CHash $path; bytes = [IO.FileInfo]::new($path).Length } }
    return $pair
}
function Test-CPair($Left, $Right) {
    foreach ($name in $script:Names) {
        $leftEntry = if ($Left -is [Collections.IDictionary]) { $Left[$name] } else { $Left.PSObject.Properties[$name].Value }
        $rightEntry = if ($Right -is [Collections.IDictionary]) { $Right[$name] } else { $Right.PSObject.Properties[$name].Value }
        if ($leftEntry.sha256 -cne $rightEntry.sha256 -or [Int64]$leftEntry.bytes -ne [Int64]$rightEntry.bytes) { return $false }
    }
    return $true
}
function Get-CObservedExe([string]$Game) { return [ordered]@{ sha256 = Get-CHash (Join-Path $Game 'CryptCustodian.exe'); bytes = [IO.FileInfo]::new((Join-Path $Game 'CryptCustodian.exe')).Length } }
function Enter-CLock([string]$Game) {
    $bytes = [Text.Encoding]::UTF8.GetBytes((Get-CFullPath $Game).ToUpperInvariant()); $sha = [Security.Cryptography.SHA256]::Create()
    try { $name = 'Global\CryptCustodianPL-C-' + ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '') } finally { $sha.Dispose() }
    $mutex = [Threading.Mutex]::new($false, $name)
    try { try { $got = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $got = $true }; if (-not $got) { throw 'Inna operacja instalatora jest w toku (lock).' }; return $mutex } catch { $mutex.Dispose(); throw }
}
function Assert-CStateLocation($C, [string]$Game) {
    $state = (Get-CFullPath $C.State) + '\\'; $gamePrefix = (Get-CFullPath $Game) + '\\'
    if ($state.StartsWith($gamePrefix, [StringComparison]::OrdinalIgnoreCase) -or $gamePrefix.StartsWith($state, [StringComparison]::OrdinalIgnoreCase)) { throw 'Kopie zapasowe muszą być poza katalogiem gry.' }
    Assert-CSafePath $state
}
function Write-CComplete([string]$Dir) { $hash = Get-CHash (Join-Path $Dir 'receipt.json'); Write-CNewBytes (Join-Path $Dir 'complete') ([Text.Encoding]::ASCII.GetBytes($hash)) }
function Assert-CReceipt($C, [string]$Game, [string]$Dir, [switch]$RequireComplete) {
    Assert-CSafePath $Dir; $receiptPath = Join-Path $Dir 'receipt.json'
    if (-not [IO.File]::Exists($receiptPath)) { throw 'Brak receipt transakcji.' }
    if ($RequireComplete) { $marker = Join-Path $Dir 'complete'; if (-not [IO.File]::Exists($marker) -or [IO.File]::ReadAllText($marker).Trim() -cne (Get-CHash $receiptPath)) { throw 'Receipt nie jest kompletny.' } }
    $r = [IO.File]::ReadAllText($receiptPath) | ConvertFrom-Json
    if ($r.schema -cne 'ccpl-c-receipt-v1' -or $r.appId -cne $C.AppId -or $r.version -cne $C.Version -or $r.gamePath -ine $Game -or $r.manifestHash -cne $C.ManifestHash -or -not $r.transactionId) { throw 'Niezgodny receipt C.' }
    return $r
}
function Assert-CNoIncomplete($C, [string]$Game) {
    foreach ($parentName in @('backups', 'operations')) { $parent = Join-Path $C.State $parentName; if (-not [IO.Directory]::Exists($parent)) { continue }; foreach ($dir in [IO.Directory]::GetDirectories($parent)) { try { $r = Assert-CReceipt $C $Game $dir; if (-not [IO.File]::Exists((Join-Path $dir 'complete'))) { throw 'Nieukończona transakcja C; odmowa automatycznego odzyskiwania.' } } catch { if ($_.Exception.Message -match 'Nieukończona') { throw } } } }
}
function Replace-CVerified([string]$Source, [string]$Target, [string]$Before, [string]$After) {
    if ((Get-CHash $Source) -cne $After -or (Get-CHash $Target) -cne $Before) { throw 'TOCTOU: plik zmienił się przed podmianą.' }
    [IO.File]::Replace($Source, $Target, [Management.Automation.Language.NullString]::Value, $false)
    if ((Get-CHash $Target) -cne $After) { throw 'Błąd readback po podmianie.' }
}
function Find-CReceipt($C, [string]$Game) {
    $parent = Join-Path $C.State 'backups'; if (-not [IO.Directory]::Exists($parent)) { throw 'Brak kompletnej kopii zapasowej C.' }
    $valid = @(); foreach ($dir in [IO.Directory]::GetDirectories($parent)) { try { $r = Assert-CReceipt $C $Game $dir -RequireComplete; $valid += [pscustomobject]@{ Dir = $dir; Receipt = $r; Time = [IO.File]::GetLastWriteTimeUtc((Join-Path $dir 'complete')) } } catch { } }
    if ($valid.Count -eq 0) { throw 'Brak kompletnego zgodnego receipt C.' }; return ($valid | Sort-Object Time -Descending | Select-Object -First 1)
}
function Invoke-CInstall($C, [string]$Game, $Before) {
    $id = [Guid]::NewGuid().ToString('N'); $backup = New-COwnedDirectory (Join-Path $C.State 'backups') $id
    $intent = [ordered]@{ schema = 'ccpl-c-receipt-v1'; status = 'intent'; appId = $C.AppId; version = $C.Version; gamePath = $Game; manifestHash = $C.ManifestHash; transactionId = $id; exeObserved = Get-CObservedExe $Game; before = $Before; after = $null }
    Write-CNewJson (Join-Path $backup 'receipt.json') $intent
    foreach ($name in $script:Names) { Copy-CVerified (Join-Path $Game $name) (Join-Path $backup $name) $Before[$name].sha256 }
    $stage = New-COwnedDirectory $Game ('.ccpl-c-stage-' + $id); $done = @()
    try {
        foreach ($name in $script:Names) { Copy-CVerified (Join-Path (Join-Path $C.Package 'GameFiles') $name) (Join-Path $stage $name) $C.Payload[$name] }
        if (-not (Test-CPair $Before (Get-CPair $Game))) { throw 'TOCTOU: para plików zmieniła się przed commit.' }
        foreach ($name in $script:Names) { Replace-CVerified (Join-Path $stage $name) (Join-Path $Game $name) $Before[$name].sha256 $C.Payload[$name]; $done += $name; if ($C.Test -and $C.Fault -ceq 'AfterFirst' -and $done.Count -eq 1) { throw 'Injected failure after first replace' } }
        $after = Get-CPair $Game
        $final = [ordered]@{ schema = 'ccpl-c-receipt-v1'; status = 'committed'; appId = $C.AppId; version = $C.Version; gamePath = $Game; manifestHash = $C.ManifestHash; transactionId = $id; exeObserved = Get-CObservedExe $Game; before = $Before; after = $after }
        [IO.File]::Delete((Join-Path $backup 'receipt.json')); Write-CNewJson (Join-Path $backup 'receipt.json') $final; Write-CComplete $backup
        return 'install-ok'
    } catch {
        $failure = $_.Exception.Message; $errors = @()
        for ($index = $done.Count - 1; $index -ge 0; $index--) { $name = $done[$index]; try { if ((Get-CHash (Join-Path $Game $name)) -ceq $C.Payload[$name]) { $rb = Join-Path $stage ('rollback-' + $name); Copy-CVerified (Join-Path $backup $name) $rb $Before[$name].sha256; Replace-CVerified $rb (Join-Path $Game $name) $C.Payload[$name] $Before[$name].sha256 } else { throw 'Nieznany cel rollback.' } } catch { $errors += $_.Exception.Message } }
        if ($errors.Count) { throw "$failure | recovery-required: $($errors -join '; ')" }; throw "$failure | rolled-back"
    } finally { }
}
function Invoke-CUninstall($C, [string]$Game) {
    $found = Find-CReceipt $C $Game; $receipt = $found.Receipt; $current = Get-CPair $Game
    if (-not (Test-CPair $current $receipt.after)) { throw 'Odinstalowanie wymaga dokładnej pary after z receipt C.' }
    foreach ($name in $script:Names) { if ((Get-CHash (Join-Path $found.Dir $name)) -cne $receipt.before.$name.sha256 -or [IO.FileInfo]::new((Join-Path $found.Dir $name)).Length -ne [Int64]$receipt.before.$name.bytes) { throw 'Backup nie odpowiada before z receipt C.' } }
    $id = [Guid]::NewGuid().ToString('N'); $operation = New-COwnedDirectory (Join-Path $C.State 'operations') $id
    Write-CNewJson (Join-Path $operation 'receipt.json') ([ordered]@{ schema='ccpl-c-receipt-v1';status='intent';appId=$C.AppId;version=$C.Version;gamePath=$Game;manifestHash=$C.ManifestHash;transactionId=$id;exeObserved=(Get-CObservedExe $Game);before=$current;after=$receipt.before })
    $stage = New-COwnedDirectory $Game ('.ccpl-c-stage-' + $id); $done = @()
    try { foreach ($name in $script:Names) { Copy-CVerified (Join-Path $found.Dir $name) (Join-Path $stage $name) $receipt.before.$name.sha256 }; foreach ($name in $script:Names) { Replace-CVerified (Join-Path $stage $name) (Join-Path $Game $name) $receipt.after.$name.sha256 $receipt.before.$name.sha256; $done += $name }; if (-not (Test-CPair (Get-CPair $Game) $receipt.before)) { throw 'Błąd readback uninstall.' }; [IO.File]::Delete((Join-Path $operation 'receipt.json')); Write-CNewJson (Join-Path $operation 'receipt.json') ([ordered]@{ schema='ccpl-c-receipt-v1';status='committed';appId=$C.AppId;version=$C.Version;gamePath=$Game;manifestHash=$C.ManifestHash;transactionId=$id;exeObserved=(Get-CObservedExe $Game);before=$current;after=$receipt.before }); Write-CComplete $operation; return 'uninstall-ok' } catch { throw $_ } finally { }
}
function Invoke-CTransaction($C, [string]$Game, [string]$Mode) {
    Assert-CPackage $C; $Game = Assert-CTarget $Game; $before = Get-CPair $Game
    if ($Mode -ceq 'Preflight') { return [ordered]@{ result='preflight'; gamePath=$Game; exeObserved=(Get-CObservedExe $Game); before=$before } }
    Assert-CStateLocation $C $Game; $lock = Enter-CLock $Game
    try { Assert-CNoIncomplete $C $Game; if ($Mode -ceq 'Install') { return Invoke-CInstall $C $Game $before }; return Invoke-CUninstall $C $Game } finally { $lock.ReleaseMutex(); $lock.Dispose() }
}

try {
    if ($SelfTest) { & (Join-Path $PSScriptRoot 'selftest_c.ps1') -InstallerPath $PSCommandPath; exit $LASTEXITCODE }
    $context = Get-CProfile
    if (-not $GamePath) { throw 'Podaj -GamePath; automatyczne wykrywanie nie jest częścią C-Force.' }
    if ($Action -ceq 'Install' -and -not $AcceptVersionRisk) { throw 'C-Force wymaga jawnego -AcceptVersionRisk; -NonInteractive go nie zastępuje.' }
    $result = Invoke-CTransaction $context $GamePath $Action
    if ($Action -ceq 'Preflight') { Write-Host ($result | ConvertTo-Json -Depth 8) } else { Write-Host "Wynik: $result" }
    exit 0
} catch { Write-Error -ErrorAction Continue $_.Exception.Message; exit 1 }
