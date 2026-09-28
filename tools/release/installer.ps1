#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$GamePath,
    [ValidateSet('Install','Uninstall','Preflight')][string]$Action = 'Install',
    [switch]$NonInteractive,
    [switch]$SelfTest
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:PackageRoot = $PSScriptRoot
$script:Names = @('data.win', 'translations.ini')

function Get-Hash([string]$Path) {
    Assert-SafePath $Path
    $s = [IO.File]::Open($Path, 'Open', 'Read', 'Read')
    $h = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($h.ComputeHash($s))).Replace('-', '').ToLowerInvariant() }
    finally { $h.Dispose(); $s.Dispose() }
}
function Get-TextHash([string]$Text) {
    $h = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($h.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text)))).Replace('-', '').ToLowerInvariant() }
    finally { $h.Dispose() }
}
function Get-FullPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or $Path -notmatch '^[a-zA-Z]:[\\/]') { throw 'Wymagana jest lokalna, bezwzględna ścieżka z literą dysku.' }
    if ($Path.Substring(2).Contains(':')) { throw 'Alternatywne strumienie danych (ADS) nie są obsługiwane.' }
    $p = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    foreach ($part in $p.Substring(3).Split('\')) {
        if ($part.EndsWith('.') -or $part.EndsWith(' ')) { throw 'Ścieżka jest niejednoznaczna.' }
    }
    return $p
}
function Assert-SafePath([string]$Path) {
    $p = Get-FullPath $Path
    while ($p) {
        try { $a = [IO.File]::GetAttributes($p) }
        catch [IO.FileNotFoundException] { $a = 0 }
        catch [IO.DirectoryNotFoundException] { $a = 0 }
        if (($a -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Niedozwolone przekierowanie ścieżki (reparse point): $p" }
        $p = [IO.Path]::GetDirectoryName($p)
    }
}
function Assert-Hash([string]$Path, [string]$Expected) {
    if ((Get-Hash $Path) -cne $Expected) { throw "Niezgodny SHA-256: $Path" }
}
function Write-NewBytes([string]$Path, [byte[]]$Bytes) {
    Assert-SafePath $Path
    $f = [IO.File]::Open($Path, 'CreateNew', 'Write', 'None')
    try { $f.Write($Bytes, 0, $Bytes.Length); $f.Flush($true) } finally { $f.Dispose() }
}
function Write-NewText([string]$Path, [string]$Text) {
    Write-NewBytes $Path ([Text.UTF8Encoding]::new($false).GetBytes($Text))
}
function Write-NewJson([string]$Path, $Value) {
    Write-NewText $Path ($Value | ConvertTo-Json -Depth 12 -Compress)
}
function Copy-Verified([string]$Source, [string]$Destination, [string]$Expected) {
    Assert-Hash $Source $Expected
    Assert-SafePath $Destination
    $inputStream = [IO.File]::Open($Source, 'Open', 'Read', 'Read')
    try {
        $outputStream = [IO.File]::Open($Destination, 'CreateNew', 'Write', 'None')
        try { $inputStream.CopyTo($outputStream); $outputStream.Flush($true) } finally { $outputStream.Dispose() }
    } finally { $inputStream.Dispose() }
    Assert-Hash $Destination $Expected
}
function Ensure-Directory([string]$Path) {
    Assert-SafePath $Path
    [void][IO.Directory]::CreateDirectory($Path)
    Assert-SafePath $Path
}
function New-OwnedDirectory([string]$Parent, [string]$Id) {
    # Reservation and all files are CreateNew; never merge an existing directory.
    Assert-SafePath $Parent
    $p = Join-Path $Parent $Id
    if ([IO.Directory]::Exists($p) -or [IO.File]::Exists($p)) { throw 'Katalog transakcji już istnieje.' }
    Write-NewText ($p + '.reservation') $Id
    if ([IO.Directory]::Exists($p) -or [IO.File]::Exists($p)) { throw 'Kolizja katalogu transakcji.' }
    [void][IO.Directory]::CreateDirectory($p)
    Assert-SafePath $p
    Write-NewText (Join-Path $p '.owner') $Id
    return $p
}
function Get-Profile {
    return @{
        Version = '1.0-beta'; AppId = '2394650'
        Exe = '252277ea55574e67877fe20fd4532ff97b6bc75dc514605593c6788b1fa9c0a0'
        Original = @{
            'data.win' = '15e2c8ef57f4c5f599589b5d15021281a11b73757be54d0bbf739d57dd280b99'
            'translations.ini' = '09d193cf8ef131c4bf9127bab49aeb9bf4c5cea88113a32d2c88813bd0dd286f'
        }
        Patched = @{
            'data.win' = '01cb062f18456f4b44385944f21534b88670e2769a22f48cb6f01d895e64e73e'
            'translations.ini' = 'deeda976d7bf414bce0fe1af7d0f9719efa990bb83d4c956f989dfc886dc055f'
        }
        ManifestHash = '__MANIFEST_SHA256__'
        Package = $script:PackageRoot
        State = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'CryptCustodianPolishPatch')
        Test = $false; Fault = ''; TransactionId = ''; SimulateRunning = $false
    }
}
function Assert-Package($C) {
    $root = $C.Package
    Assert-SafePath $root
    Assert-Hash (Join-Path $root 'release-manifest.json') $C.ManifestHash
    $m = [IO.File]::ReadAllText((Join-Path $root 'release-manifest.json')) | ConvertFrom-Json
    if ($m.version -cne $C.Version -or $m.appId -cne $C.AppId -or $m.exeSha256 -cne $C.Exe) { throw 'Manifest: niezgodny profil.' }
    if (@($m.payload).Count -ne 2) { throw 'Manifest: nieprawidłowa liczba plików instalacyjnych.' }
    foreach ($name in $script:Names) {
        $entry = @($m.payload | Where-Object { $_.name -ceq $name })
        if ($entry.Count -ne 1 -or $entry[0].sha256 -cne $C.Patched[$name] -or $entry[0].originalSha256 -cne $C.Original[$name]) { throw 'Manifest: niezgodne sumy kontrolne SHA-256.' }
        $p = Join-Path $root ('GameFiles\' + $name)
        Assert-Hash $p $C.Patched[$name]
        if ([IO.FileInfo]::new($p).Length -ne $entry[0].bytes) { throw 'Manifest: niezgodny rozmiar pliku instalacyjnego.' }
    }
    if (-not $C.Test) {
        $expected = @('CZYTAJ_MNIE.txt','instaluj.bat','instaluj.ps1','odinstaluj.bat','odinstaluj.ps1','GameFiles/data.win','GameFiles/translations.ini','release-manifest.json','SHA256SUMS.txt','LICENSE-NOTICE.txt')
        # Enumerate one level at a time, rejecting reparse directories BEFORE descent.
        $found = @(Get-PackageFiles $root '')
        if (@(Compare-Object ($expected | Sort-Object) ($found | Sort-Object) -CaseSensitive).Count -ne 0) { throw 'Paczka jest niepełna lub zawiera dodatkowe pliki. Rozpakuj ją do pustego katalogu.' }
        $seen = @{}
        foreach ($line in [IO.File]::ReadAllLines((Join-Path $root 'SHA256SUMS.txt'))) {
            if ($line -cnotmatch '^([0-9a-f]{64})  (.+)$') { throw 'Niepoprawna lista SHA256SUMS.' }
            $hash = $Matches[1]; $name = $Matches[2]
            if ($name -ceq 'SHA256SUMS.txt' -or $expected -cnotcontains $name -or $seen.ContainsKey($name)) { throw 'Niepoprawna nazwa w SHA256SUMS.' }
            $seen[$name] = $true
            Assert-Hash (Join-Path $root $name) $hash
        }
        if ($seen.Count -ne 9) { throw 'Niepełna lista SHA256SUMS.' }
    }
}
function Get-PackageFiles([string]$Root, [string]$Relative) {
    $p = $Root
    if ($Relative) { $p = Join-Path $Root $Relative }
    Assert-SafePath $p
    foreach ($child in [IO.Directory]::EnumerateFileSystemEntries($p)) {
        Assert-SafePath $child
        $name = [IO.Path]::GetFileName($child)
        $rel = $name
        if ($Relative) { $rel = $Relative + '/' + $name }
        if ([IO.Directory]::Exists($child)) { Get-PackageFiles $Root $rel } else { $rel }
    }
}
function Assert-GameClosed($C) {
    if ($C.Test -and $C.SimulateRunning) { throw 'Gra jest uruchomiona (symulacja).' }
    $processes = [Diagnostics.Process]::GetProcessesByName('CryptCustodian')
    try { if ($processes.Length -gt 0) { throw 'Zamknij Crypt Custodian przed rozpoczęciem operacji.' } }
    finally { foreach ($p in $processes) { $p.Dispose() } }
}
function Get-PairState($C, [string]$Game) {
    Assert-SafePath $Game
    Assert-Hash (Join-Path $Game 'CryptCustodian.exe') $C.Exe
    $original = $true; $patched = $true
    foreach ($name in $script:Names) {
        $hash = Get-Hash (Join-Path $Game $name)
        $original = $original -and ($hash -ceq $C.Original[$name])
        $patched = $patched -and ($hash -ceq $C.Patched[$name])
    }
    if ($original) { return 'original' }
    if ($patched) { return 'patched' }
    throw 'Nieznana lub mieszana para plików gry. Nie wprowadzono zmian. Użyj opcji „Sprawdź spójność plików” w Steam; nie wymuszaj powrotu do starszej wersji gry.'
}
function Get-LockName([string]$Game) {
    return 'Global\CryptCustodianPL-' + (Get-TextHash ((Get-FullPath $Game).ToUpperInvariant()))
}
function Enter-GameLock([string]$Game) {
    $m = [Threading.Mutex]::new($false, (Get-LockName $Game))
    try {
        try { $got = $m.WaitOne(0) } catch [Threading.AbandonedMutexException] { $got = $true }
        if (-not $got) { throw 'Inna operacja instalatora jest w toku (lock).' }
        return $m
    } catch { $m.Dispose(); throw }
}
function Assert-StateLocation($C, [string]$Game) {
    $state = (Get-FullPath $C.State) + '\'
    $gamePrefix = (Get-FullPath $Game) + '\'
    if ($state.StartsWith($gamePrefix, [StringComparison]::OrdinalIgnoreCase) -or $gamePrefix.StartsWith($state, [StringComparison]::OrdinalIgnoreCase)) { throw 'Katalog kopii zapasowych musi być poza katalogiem gry i nie może go zawierać.' }
    if (-not $C.Test -and $state.StartsWith(([IO.Path]::GetTempPath().TrimEnd('\') + '\'), [StringComparison]::OrdinalIgnoreCase)) { throw 'Kopie zapasowe nie mogą znajdować się w katalogu tymczasowym (Temp).' }
    Assert-SafePath $state
}
function Write-Event($J, [string]$Status, [string]$Name = '') {
    $J.Sequence++
    Write-NewJson (Join-Path $J.Path ('event-{0:D4}.json' -f $J.Sequence)) ([ordered]@{
        version = '1.0-beta'; action = $J.Action; status = $Status; file = $Name; utc = [DateTime]::UtcNow.ToString('o')
    })
}
function New-Backup($C, [string]$Game, [string]$Id) {
    $parent = Join-Path $C.State 'backups'
    Ensure-Directory $parent
    $dir = New-OwnedDirectory $parent $Id
    foreach ($name in $script:Names) { Copy-Verified (Join-Path $Game $name) (Join-Path $dir $name) $C.Original[$name] }
    Copy-Verified (Join-Path $C.Package 'release-manifest.json') (Join-Path $dir 'release-manifest.json') $C.ManifestHash
    $receipt = [ordered]@{
        schema = 'ccpl-backup-v1'; version = $C.Version; appId = $C.AppId
        id = $Id; gamePath = $Game; exeSha256 = $C.Exe
        original = $C.Original; patched = $C.Patched
    }
    Write-NewJson (Join-Path $dir 'receipt.json') $receipt
    $receiptHash = Get-Hash (Join-Path $dir 'receipt.json')
    Write-NewText (Join-Path $dir 'complete') $receiptHash
    Assert-Backup $C $Game $dir
    return $dir
}
function Assert-Backup($C, [string]$Game, [string]$Dir) {
    Assert-SafePath $Dir
    Assert-Hash (Join-Path $Dir 'release-manifest.json') $C.ManifestHash
    $receiptPath = Join-Path $Dir 'receipt.json'
    Assert-SafePath (Join-Path $Dir 'complete')
    Assert-Hash $receiptPath ([IO.File]::ReadAllText((Join-Path $Dir 'complete')))
    $r = [IO.File]::ReadAllText($receiptPath) | ConvertFrom-Json
    if ($r.schema -cne 'ccpl-backup-v1' -or $r.version -cne $C.Version -or $r.appId -cne $C.AppId -or $r.exeSha256 -cne $C.Exe -or $r.gamePath -ine $Game -or $r.id -cne [IO.Path]::GetFileName($Dir)) { throw 'Niezgodne metadane kopii zapasowej (receipt.json).' }
    foreach ($name in $script:Names) {
        if ($r.original.$name -cne $C.Original[$name] -or $r.patched.$name -cne $C.Patched[$name]) { throw 'Niezgodny profil kopii zapasowej.' }
        Assert-Hash (Join-Path $Dir $name) $C.Original[$name]
    }
}
function Find-Backup($C, [string]$Game) {
    $parent = Join-Path $C.State 'backups'
    Assert-SafePath $parent
    if ([IO.Directory]::Exists($parent)) {
        foreach ($dir in ([IO.Directory]::GetDirectories($parent) | Sort-Object -Descending)) {
            if ([IO.Path]::GetFileName($dir) -notmatch '^[0-9a-f]{32}$') { continue }
            try { Assert-Backup $C $Game $dir; return $dir } catch { Write-Warning 'Pominięto niekompletną, obcą lub uszkodzoną kopię zapasową.' }
        }
    }
    throw 'Brak kompletnej kopii zapasowej oryginalnych plików tej instalacji gry (original). Nie podmieniono plików gry. Użyj opcji „Sprawdź spójność plików” w Steam.'
}
function Replace-Verified($C, [string]$Source, [string]$Target, [string]$Before, [string]$After) {
    Assert-GameClosed $C
    Assert-Hash $Source $After
    Assert-Hash $Target $Before
    # NO delete+copy fallback. Source and target must share a volume.
    # PS5.1 binds ordinary $null to an empty string for this string argument.
    [IO.File]::Replace($Source, $Target, [System.Management.Automation.Language.NullString]::Value, $false)
    Assert-Hash $Target $After
}
function Remove-OwnedStage([string]$Stage, [string]$Id, $C) {
    # Unknown content is preserved, including after an interrupted/failed operation.
    Assert-SafePath $Stage
    $owner = Join-Path $Stage '.owner'
    Assert-SafePath $owner
    if ([IO.File]::ReadAllText($owner) -cne $Id) { throw 'Nieznany właściciel katalogu roboczego (staging).' }
    $deletable = @()
    foreach ($p in [IO.Directory]::EnumerateFileSystemEntries($Stage)) {
        Assert-SafePath $p
        $n = [IO.Path]::GetFileName($p)
        if ($n -ceq '.owner') { continue }
        $baseName = $n -replace '^rollback-', ''
        if ($script:Names -cnotcontains $baseName -or [IO.Directory]::Exists($p)) { throw 'Nieznany plik w katalogu roboczym (staging); zachowano katalog.' }
        $h = Get-Hash $p
        if ($h -cne $C.Original[$baseName] -and $h -cne $C.Patched[$baseName]) { throw 'Nieznana suma kontrolna pliku w katalogu roboczym (staging); zachowano katalog.' }
        $deletable += $p
    }
    foreach ($p in $deletable) { Assert-SafePath $p; [IO.File]::Delete($p) }
    [IO.File]::Delete($owner)
    [IO.Directory]::Delete($Stage, $false)
    $reservation = $Stage + '.reservation'
    Assert-SafePath $reservation
    if ([IO.File]::ReadAllText($reservation) -ceq $Id) { [IO.File]::Delete($reservation) }
}
function Invoke-Patch($C, [string]$Game, [string]$Mode) {
    $Game = Get-FullPath $Game
    Assert-StateLocation $C $Game
    Assert-SafePath $Game
    $lock = Enter-GameLock $Game
    $stage = $null; $journal = $null
    try {
        if ($C.Test -and $C.Fault -ceq 'HoldLock') {
            Write-NewText $C.LockReady 'locked'
            $until = [DateTime]::UtcNow.AddSeconds(30)
            while (-not [IO.File]::Exists($C.LockRelease) -and [DateTime]::UtcNow -lt $until) { [Threading.Thread]::Sleep(50) }
            if (-not [IO.File]::Exists($C.LockRelease)) { throw 'SelfTest lock timeout.' }
        }
        Assert-Package $C
        Assert-GameClosed $C
        $pair = Get-PairState $C $Game
        if ($Mode -ceq 'Preflight') { return "preflight:$pair" }
        if ($Mode -ceq 'Install' -and $pair -ceq 'patched') { return 'already-installed' }
        if ($Mode -ceq 'Uninstall' -and $pair -cne 'patched') { throw 'Odinstalowanie wymaga obu plików zgodnych z tym spolszczeniem (PL). Nie wprowadzono zmian.' }
        $id = [Guid]::NewGuid().ToString('N')
        if ($C.Test -and $C.TransactionId) { $id = $C.TransactionId }
        Ensure-Directory $C.State
        if ($Mode -ceq 'Install') {
            $backup = New-Backup $C $Game $id
            $operation = $backup
            $from = $C.Original; $to = $C.Patched
            $sourceDir = Join-Path $C.Package 'GameFiles'; $rollbackDir = $backup
        } else {
            $backup = Find-Backup $C $Game
            $parent = Join-Path $C.State 'operations'
            Ensure-Directory $parent
            $operation = New-OwnedDirectory $parent $id
            $from = $C.Patched; $to = $C.Original
            $sourceDir = $backup; $rollbackDir = Join-Path $C.Package 'GameFiles'
        }
        $journal = @{ Path = $operation; Sequence = 0; Action = $Mode }
        Write-NewJson (Join-Path $operation 'transaction.json') ([ordered]@{ version = $C.Version; gamePath = $Game; backupId = [IO.Path]::GetFileName($backup); action = $Mode; id = $id })
        Write-Event $journal 'prepared'
        $stage = New-OwnedDirectory $Game ('.ccpl-stage-' + $id)
        foreach ($name in $script:Names) { Copy-Verified (Join-Path $sourceDir $name) (Join-Path $stage $name) $to[$name] }
        if ((Get-PairState $C $Game) -cne $pair) { throw 'Pliki gry zmieniły się przed rozpoczęciem podmiany.' }
        $attempted = @()
        try {
            foreach ($name in $script:Names) {
                Assert-Backup $C $Game $backup
                Assert-Hash (Join-Path $Game 'CryptCustodian.exe') $C.Exe
                Write-Event $journal 'replace-intent' $name
                $attempted += $name
                Replace-Verified $C (Join-Path $stage $name) (Join-Path $Game $name) $from[$name] $to[$name]
                Write-Event $journal 'replaced' $name
                if ($C.Test -and $attempted.Count -eq 1) {
                    if ($C.Fault -ceq 'UnknownRollback') { [IO.File]::WriteAllText((Join-Path $Game $name), 'foreign change'); throw 'Injected unknown rollback target' }
                    if ($C.Fault -ceq 'CorruptRollbackBackup') { [IO.File]::WriteAllText((Join-Path $backup $name), 'damaged backup'); throw 'Injected backup corruption' }
                    if ($C.Fault -ceq 'AfterFirst') { throw 'Injected failure after first replace' }
                }
            }
            foreach ($name in $script:Names) { Assert-Hash (Join-Path $Game $name) $to[$name] }
            Write-Event $journal 'committed'
            Write-NewJson (Join-Path $operation 'result.json') @{ status = 'committed'; version = $C.Version; action = $Mode }
            return ($Mode.ToLowerInvariant() + '-ok')
        } catch {
            $failure = $_.Exception.Message
            $recoveryErrors = @()
            # Record intent before each change, but do not let a journal I/O error
            # prevent a guarded rollback of a replacement that already succeeded.
            for ($i = $attempted.Count - 1; $i -ge 0; $i--) {
                $name = $attempted[$i]
                try {
                    $current = Get-Hash (Join-Path $Game $name)
                    if ($current -ceq $from[$name]) { continue }
                    if ($current -cne $to[$name]) { throw 'Nieznany plik docelowy podczas wycofywania zmian (rollback): zachowano go bez nadpisania.' }
                    Assert-Hash (Join-Path $Game 'CryptCustodian.exe') $C.Exe
                    Assert-Hash (Join-Path $rollbackDir $name) $from[$name]
                    $rb = Join-Path $stage ('rollback-' + $name)
                    Copy-Verified (Join-Path $rollbackDir $name) $rb $from[$name]
                    Replace-Verified $C $rb (Join-Path $Game $name) $to[$name] $from[$name]
                } catch { $recoveryErrors += $_.Exception.Message }
            }
            $status = 'rolled-back'
            if ($recoveryErrors.Count) { $status = 'recovery-required' }
            try { Write-Event $journal $status } catch { Write-Warning 'Błąd zapisu dziennika; zachowaj kopię zapasową i katalog roboczy (staging).' }
            throw "$failure | $(Get-StatusDescription $status) | $($recoveryErrors -join '; ') | Kopia zapasowa: $backup"
        }
    } finally {
        if ($stage) {
            try { Remove-OwnedStage $stage ('.ccpl-stage-' + $id) $C } catch { Write-Warning $_.Exception.Message }
        }
        $lock.ReleaseMutex(); $lock.Dispose()
    }
}
function Get-SteamCandidates {
    $roots = @()
    foreach ($key in @('HKCU:\Software\Valve\Steam','HKLM:\SOFTWARE\Valve\Steam','HKLM:\SOFTWARE\WOW6432Node\Valve\Steam')) {
        try {
            $r = Get-ItemProperty -LiteralPath $key
            foreach ($prop in @('SteamPath','InstallPath')) {
                if ($r.PSObject.Properties[$prop] -and $r.$prop) { $roots += [string]$r.$prop }
            }
        } catch { }
    }
    $libs = @($roots)
    foreach ($root in $roots) {
        $vdf = Join-Path $root 'steamapps\libraryfolders.vdf'
        if ([IO.File]::Exists($vdf)) {
            Assert-SafePath $vdf
            $text = [IO.File]::ReadAllText($vdf)
            foreach ($m in [regex]::Matches($text, '"(?:path|[0-9]+)"\s*"([^"]+)"')) {
                $value = $m.Groups[1].Value.Replace('\\', '\')
                if ($value -match '^[a-zA-Z]:[\\/]') { $libs += $value }
            }
        }
    }
    foreach ($lib in ($libs | Sort-Object -Unique)) {
        $acf = Join-Path $lib 'steamapps\appmanifest_2394650.acf'
        if (-not [IO.File]::Exists($acf)) { continue }
        Assert-SafePath $acf
        $text = [IO.File]::ReadAllText($acf)
        if ($text -notmatch '"appid"\s*"2394650"') { continue }
        if ($text -match '"installdir"\s*"([^"\\/:]+)"') {
            $dir = $Matches[1]
            if ($dir -eq '.' -or $dir -eq '..') { continue }
            Join-Path (Join-Path $lib 'steamapps\common') $dir
        }
    }
}
function Select-Game($C) {
    foreach ($candidate in @(Get-SteamCandidates)) {
        try { $pair = Get-PairState $C $candidate } catch { Write-Warning 'Pominięto nieobsługiwaną instalację Steam.'; continue }
        if ((Read-Host "Wykryto: $candidate — $(Get-StatusDescription $pair). Użyć tej instalacji? [T/n]") -notmatch '^[nN]') { return $candidate }
    }
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $dialog = [Windows.Forms.FolderBrowserDialog]::new()
        try {
            $dialog.Description = 'Wskaż katalog zawierający CryptCustodian.exe'; $dialog.ShowNewFolderButton = $false
            if ($dialog.ShowDialog() -eq [Windows.Forms.DialogResult]::OK) { return $dialog.SelectedPath }
        } finally { $dialog.Dispose() }
    } catch { Write-Warning 'Okno wyboru katalogu jest niedostępne.' }
    return (Read-Host 'Podaj bezwzględną ścieżkę do katalogu gry (puste pole = anuluj)').Trim().Trim('"')
}

function Get-StatusDescription([string]$Status) {
    switch -CaseSensitive ($Status) {
        'original' { return 'Oryginalne pliki obsługiwanej wersji gry (original)' }
        'patched' { return 'Pliki zgodne z tym spolszczeniem (PL / patched)' }
        'preflight:original' { return 'Kontrola wstępna zakończona: oryginalne pliki obsługiwanej wersji gry; bez zmian (preflight:original)' }
        'preflight:patched' { return 'Kontrola wstępna zakończona: pliki zgodne z tym spolszczeniem; bez zmian (preflight:patched)' }
        'already-installed' { return 'Spolszczenie jest już zainstalowane; nie utworzono nowej kopii zapasowej (already-installed)' }
        'install-ok' { return 'Spolszczenie zostało zainstalowane (install-ok)' }
        'uninstall-ok' { return 'Spolszczenie zostało odinstalowane; przywrócono oryginalne pliki gry (uninstall-ok)' }
        'rolled-back' { return 'Wycofano zmiany po błędzie (rolled-back)' }
        'recovery-required' { return 'Nie udało się wycofać wszystkich zmian; nie uruchamiaj gry, zachowaj raport i kopię zapasową (recovery-required)' }
        'Install' { return 'Instalacja (Install)' }
        'Uninstall' { return 'Odinstalowanie (Uninstall)' }
        default { return $Status }
    }
}

# Self-test implementation is inserted by the release builder. Only synthetic
# fixtures may change the profile; there is no CLI hash/state override.
__SELF_TEST_FUNCTIONS__

try {
    if ($SelfTest) {
        if ($GamePath) { throw 'Testy na plikach testowych (SelfTest) nie przyjmują ścieżki gry.' }
        Invoke-SelfTests
        exit 0
    }
    $context = Get-Profile
    Assert-Package $context
    if (-not $GamePath) {
        if ($NonInteractive) { throw 'Tryb nieinteraktywny wymaga -GamePath.' }
        $GamePath = Select-Game $context
    }
    if (-not $GamePath) { throw 'Anulowano.' }
    $GamePath = Get-FullPath $GamePath
    if ($Action -cne 'Preflight' -and -not $NonInteractive) {
        $pre = Invoke-Patch $context $GamePath 'Preflight'
        Write-Host "Crypt Custodian PL 1.0-beta | $(Get-StatusDescription $pre)"
        Write-Warning 'Zamknij grę ORAZ Steam; wstrzymaj aktualizacje. Nie uruchamiaj ich, gdy operacja jest w toku.'
        if ((Read-Host "Operacja: $(Get-StatusDescription $Action). Kontynuować? [t/N]") -notmatch '^[tT]$') { throw 'Anulowano.' }
    }
    $result = Invoke-Patch $context $GamePath $Action
    Write-Host "Wynik: $(Get-StatusDescription $result)"
    if ($result -eq 'install-ok' -or $result -eq 'already-installed') { Write-Host 'W grze wybierz język English. Istniejące kopie zapasowe i raporty pozostają w %LOCALAPPDATA%\CryptCustodianPolishPatch.' }
    if ($result -eq 'uninstall-ok') { Write-Host 'Kopie zapasowe i raporty pozostawiono w %LOCALAPPDATA%\CryptCustodianPolishPatch. Zapisy gry nie zostały zmienione.' }
    exit 0
} catch {
    Write-Error -ErrorAction Continue $_.Exception.Message
    exit 1
}
