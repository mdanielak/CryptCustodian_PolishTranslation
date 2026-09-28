function New-Fixture([string]$Root, [string]$Name) {
    $dir = Join-Path $Root $Name
    Ensure-Directory $dir
    $game = Join-Path $dir 'Gra zażółć gęślą'
    $package = Join-Path $dir 'Paczka ćma'
    Ensure-Directory $game
    Ensure-Directory (Join-Path $package 'GameFiles')
    $c = Get-Profile
    $c.Test = $true; $c.Package = $package; $c.State = Join-Path $dir 'Stan trwały'
    Write-NewText (Join-Path $game 'CryptCustodian.exe') 'NOT AN EXECUTABLE - fixture only'
    $c.Exe = Get-Hash (Join-Path $game 'CryptCustodian.exe')
    $entries = @()
    foreach ($n in $script:Names) {
        Write-NewText (Join-Path $game $n) ("ORIGINAL $n `r`nASCII + zażółć`r`n")
        Write-NewText (Join-Path $package ('GameFiles\' + $n)) ("PL $n `r`nąćęłńóśźż ĄĆĘŁŃÓŚŹŻ`r`n")
        $c.Original[$n] = Get-Hash (Join-Path $game $n)
        $c.Patched[$n] = Get-Hash (Join-Path $package ('GameFiles\' + $n))
        $entries += @{ name = $n; sha256 = $c.Patched[$n]; originalSha256 = $c.Original[$n]; bytes = [IO.FileInfo]::new((Join-Path $package ('GameFiles\' + $n))).Length }
    }
    Write-NewJson (Join-Path $package 'release-manifest.json') @{ version = $c.Version; appId = $c.AppId; exeSha256 = $c.Exe; payload = $entries }
    $c.ManifestHash = Get-Hash (Join-Path $package 'release-manifest.json')
    return @{ C = $c; Game = $game; Root = $dir }
}
function Assert-Test($Condition, [string]$Message) {
    if (-not $Condition) { throw "TEST ASSERTION: $Message" }
}
function Expect-Refusal([scriptblock]$Block, [string]$Pattern) {
    $caught = $false
    try { & $Block | Out-Null } catch {
        $caught = $true
        Assert-Test ($_.Exception.Message -match $Pattern) ("Unexpected refusal: " + $_.Exception.Message)
    }
    Assert-Test $caught 'Expected refusal, got success'
}
function Assert-FixturePair($F, [string]$State) {
    Assert-Test ((Get-PairState $F.C $F.Game) -ceq $State) "Expected $State"
}
function Run-Case([string]$Name, [scriptblock]$Block) {
    & $Block
    $script:TestResults += @{ name = $Name; status = 'pass' }
    Write-Host "PASS $Name"
}
function Invoke-SelfTests {
    $script:TestResults = @()
    $parent = [IO.Path]::GetTempPath()
    $root = New-OwnedDirectory $parent ('ccpl-selftest-' + [Guid]::NewGuid().ToString('N'))
    Write-Host "Testy instalatora (SelfTest): wyłącznie syntetyczne pliki testowe; raport: $root"
    Run-Case 'polish-status-descriptions-preserve-technical-codes' {
        foreach ($status in @('original','patched','preflight:original','preflight:patched','already-installed','install-ok','uninstall-ok','rolled-back','recovery-required','Install','Uninstall')) {
            $message = Get-StatusDescription $status
            Assert-Test ($message -cne $status) "translated $status"
            Assert-Test ($message.Contains($status) -and $message.Contains('(') -and $message.EndsWith(')')) "technical code $status"
        }
        Assert-Test ((Get-StatusDescription 'rolled-back') -ceq 'Wycofano zmiany po błędzie (rolled-back)') 'Polish rollback message'
        Assert-Test ((Get-StatusDescription 'uninstall-ok').Contains('przywrócono oryginalne pliki gry')) 'Polish uninstall message'
    }
    Run-Case 'round-trip-byte-identical-unicode-spaces' {
        $f = New-Fixture $root 'roundtrip'
        $before = @{}
        foreach ($n in $script:Names) { $before[$n] = [Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $f.Game $n))) }
        Assert-Test ((Invoke-Patch $f.C $f.Game 'Install') -ceq 'install-ok') 'install'
        Assert-FixturePair $f 'patched'
        $backup = Find-Backup $f.C $f.Game
        Assert-Test ((Invoke-Patch $f.C $f.Game 'Uninstall') -ceq 'uninstall-ok') 'uninstall'
        foreach ($n in $script:Names) { Assert-Test ([Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $f.Game $n))) -ceq $before[$n]) "byte roundtrip $n" }
        Assert-Backup $f.C $f.Game $backup
        Assert-Test (@([IO.Directory]::GetDirectories($f.Game)).Count -eq 0) 'staging cleaned'
        Invoke-Patch $f.C $f.Game 'Install' | Out-Null
        Assert-Test (@([IO.Directory]::GetDirectories((Join-Path $f.C.State 'backups'))).Count -eq 2) 'fresh backup on reinstall'
        Assert-Backup $f.C $f.Game $backup
    }
    Run-Case 'already-installed-no-backup' {
        $f = New-Fixture $root 'already'
        foreach ($n in $script:Names) { [IO.File]::Copy((Join-Path $f.C.Package ('GameFiles\' + $n)), (Join-Path $f.Game $n), $true) }
        Assert-Test ((Invoke-Patch $f.C $f.Game 'Install') -ceq 'already-installed') 'already'
        Assert-Test (-not [IO.Directory]::Exists($f.C.State)) 'no state created'
    }
    foreach ($kind in @('unknown','mixed','missing','exe','payload','manifest','manifest-profile','process')) {
        Run-Case "refusal-$kind" {
            $f = New-Fixture $root $kind
            switch ($kind) {
                'unknown' { [IO.File]::WriteAllText((Join-Path $f.Game 'data.win'), 'unknown') }
                'mixed' { [IO.File]::Copy((Join-Path $f.C.Package 'GameFiles\data.win'), (Join-Path $f.Game 'data.win'), $true) }
                'missing' { [IO.File]::Delete((Join-Path $f.Game 'data.win')) }
                'exe' { [IO.File]::WriteAllText((Join-Path $f.Game 'CryptCustodian.exe'), 'different EXE') }
                'payload' { [IO.File]::AppendAllText((Join-Path $f.C.Package 'GameFiles\translations.ini'), 'damage') }
                'manifest' { [IO.File]::AppendAllText((Join-Path $f.C.Package 'release-manifest.json'), 'damage') }
                'manifest-profile' {
                    $p = Join-Path $f.C.Package 'release-manifest.json'
                    [IO.File]::WriteAllText($p, ([IO.File]::ReadAllText($p).Replace('1.0-beta','9.9-beta')))
                    $f.C.ManifestHash = Get-Hash $p
                }
                'process' { $f.C.SimulateRunning = $true }
            }
            $snap = @{}
            foreach ($n in @('data.win','translations.ini','CryptCustodian.exe')) {
                $p = Join-Path $f.Game $n
                if ([IO.File]::Exists($p)) { $snap[$n] = Get-Hash $p }
            }
            Expect-Refusal { Invoke-Patch $f.C $f.Game 'Install' } '.'
            foreach ($n in $snap.Keys) { Assert-Hash (Join-Path $f.Game $n) $snap[$n] }
            Assert-Test (-not [IO.Directory]::Exists($f.C.State)) 'refusal before state/backup'
        }
    }
    Run-Case 'existing-backup-no-clobber' {
        $f = New-Fixture $root 'collision'
        $f.C.TransactionId = ('a' * 32)
        $existing = Join-Path $f.C.State ('backups\' + $f.C.TransactionId)
        Ensure-Directory $existing
        Write-NewText (Join-Path $existing 'sentinel') 'do not overwrite'
        $hash = Get-Hash (Join-Path $existing 'sentinel')
        Expect-Refusal { Invoke-Patch $f.C $f.Game 'Install' } 'już istnieje'
        Assert-Hash (Join-Path $existing 'sentinel') $hash
        Assert-FixturePair $f 'original'
    }
    foreach ($mode in @('Install','Uninstall')) {
        Run-Case "rollback-after-first-$mode" {
            $f = New-Fixture $root ('rollback-' + $mode)
            $expected = 'original'
            if ($mode -ceq 'Uninstall') { Invoke-Patch $f.C $f.Game 'Install' | Out-Null; $expected = 'patched' }
            $f.C.Fault = 'AfterFirst'
            Expect-Refusal { Invoke-Patch $f.C $f.Game $mode } 'rolled-back'
            Assert-FixturePair $f $expected
            Assert-Backup $f.C $f.Game (Find-Backup $f.C $f.Game)
        }
    }
    Run-Case 'unknown-rollback-target-preserved' {
        $f = New-Fixture $root 'unknown-rollback'
        $f.C.Fault = 'UnknownRollback'
        Expect-Refusal { Invoke-Patch $f.C $f.Game 'Install' } 'recovery-required'
        Assert-Test ([IO.File]::ReadAllText((Join-Path $f.Game 'data.win')) -ceq 'foreign change') 'unknown file preserved'
        Assert-Hash (Join-Path $f.Game 'translations.ini') $f.C.Original['translations.ini']
        Assert-Backup $f.C $f.Game (Find-Backup $f.C $f.Game)
    }
    Run-Case 'real-second-replace-sharing-failure' {
        $f = New-Fixture $root 'sharing-failure'
        # Permit hash/copy reads, but deny DELETE sharing required by File.Replace.
        $handle = [IO.File]::Open((Join-Path $f.Game 'translations.ini'), 'Open', 'Read', 'Read')
        try { Expect-Refusal { Invoke-Patch $f.C $f.Game 'Install' } 'rolled-back' }
        finally { $handle.Dispose() }
        Assert-FixturePair $f 'original'
        $b = Find-Backup $f.C $f.Game
        $events = @([IO.Directory]::GetFiles($b, 'event-*.json') | ForEach-Object { [IO.File]::ReadAllText($_) | ConvertFrom-Json })
        Assert-Test (@($events | Where-Object { $_.status -ceq 'replaced' -and $_.file -ceq 'data.win' }).Count -eq 1) 'first replace really succeeded'
        Assert-Test (@($events | Where-Object { $_.status -ceq 'replace-intent' -and $_.file -ceq 'translations.ini' }).Count -eq 1) 'second replace really attempted'
    }
    Run-Case 'corrupt-backup-during-rollback-no-overwrite' {
        $f = New-Fixture $root 'bad-rollback-backup'
        $f.C.Fault = 'CorruptRollbackBackup'
        Expect-Refusal { Invoke-Patch $f.C $f.Game 'Install' } 'recovery-required'
        Assert-Hash (Join-Path $f.Game 'data.win') $f.C.Patched['data.win']
        Assert-Hash (Join-Path $f.Game 'translations.ini') $f.C.Original['translations.ini']
    }
    foreach ($kind in @('missing-backup','corrupt-backup','missing-marker','corrupt-receipt','mixed-uninstall','updated-exe')) {
        Run-Case "uninstall-refusal-$kind" {
            $f = New-Fixture $root $kind
            Invoke-Patch $f.C $f.Game 'Install' | Out-Null
            $b = Find-Backup $f.C $f.Game
            switch ($kind) {
                'missing-backup' { [IO.File]::Delete((Join-Path $b 'data.win')) }
                'corrupt-backup' { [IO.File]::AppendAllText((Join-Path $b 'data.win'), 'bad') }
                'missing-marker' { [IO.File]::Delete((Join-Path $b 'complete')) }
                'corrupt-receipt' { [IO.File]::AppendAllText((Join-Path $b 'receipt.json'), 'bad') }
                'mixed-uninstall' { [IO.File]::Copy((Join-Path $b 'data.win'), (Join-Path $f.Game 'data.win'), $true) }
                'updated-exe' { [IO.File]::WriteAllText((Join-Path $f.Game 'CryptCustodian.exe'), 'updated') }
            }
            $before = @{}
            foreach ($n in $script:Names) { $before[$n] = Get-Hash (Join-Path $f.Game $n) }
            Expect-Refusal { Invoke-Patch $f.C $f.Game 'Uninstall' } '.'
            foreach ($n in $script:Names) { Assert-Hash (Join-Path $f.Game $n) $before[$n] }
        }
    }
    Run-Case 'parallel-process-lock' {
        $f = New-Fixture $root 'parallel'
        $ready = Join-Path $f.Root 'helper-ready'
        $release = Join-Path $f.Root 'helper-release'
        $f.C.Fault = 'HoldLock'; $f.C.LockReady = $ready; $f.C.LockRelease = $release
        $serialized = [Management.Automation.PSSerializer]::Serialize(@{ Context = $f.C; Game = $f.Game; Script = (Join-Path $script:PackageRoot 'instaluj.ps1') })
        $encodedConfig = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($serialized))
        $command = @"
`$ErrorActionPreference = 'Stop'
try {
    `$config = [Management.Automation.PSSerializer]::Deserialize([Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('$encodedConfig')))
    `$tokens = `$null; `$errors = `$null
    `$ast = [Management.Automation.Language.Parser]::ParseFile(`$config.Script, [ref]`$tokens, [ref]`$errors)
    if (`$errors.Count) { throw 'Parse error in helper' }
    # Load only trusted function definitions, NEVER the production entry point.
    foreach (`$statement in `$ast.EndBlock.Statements) {
        if (`$statement -is [Management.Automation.Language.FunctionDefinitionAst]) { . ([scriptblock]::Create(`$statement.Extent.Text)) }
    }
    `$script:Names = @('data.win','translations.ini')
    Invoke-Patch `$config.Context `$config.Game 'Install' | Out-Null
    exit 0
} catch { [Console]::Error.WriteLine(`$_.Exception.Message); exit 7 }
"@
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
        $psi = [Diagnostics.ProcessStartInfo]::new()
        $psi.FileName = Join-Path $PSHOME 'powershell.exe'
        $psi.Arguments = '-NoProfile -NonInteractive -EncodedCommand ' + $encoded
        $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
        $proc = [Diagnostics.Process]::Start($psi)
        try {
            $until = [DateTime]::UtcNow.AddSeconds(15)
            while (-not [IO.File]::Exists($ready) -and [DateTime]::UtcNow -lt $until -and -not $proc.HasExited) { [Threading.Thread]::Sleep(50) }
            Assert-Test ([IO.File]::Exists($ready)) 'helper acquired named mutex'
            Expect-Refusal { Invoke-Patch $f.C $f.Game 'Install' } 'lock'
            Assert-FixturePair $f 'original'
            Assert-Test (-not [IO.Directory]::Exists($f.C.State)) 'lock prevents backup'
        } finally {
            Write-NewText $release 'release'
            [void]$proc.WaitForExit(35000)
            $helperCode = $proc.ExitCode
            $proc.Dispose()
        }
        Assert-Test ($helperCode -eq 0) 'first concurrent invocation committed'
        $f.C.Fault = ''
        Assert-FixturePair $f 'patched'
        Assert-Test ((Invoke-Patch $f.C $f.Game 'Install') -ceq 'already-installed') 'lock released'
    }
    $f = New-Fixture $root 'reparse'
    $junction = Join-Path $f.Root 'junction'
    & $env:ComSpec /d /c "mklink /J `"$junction`" `"$($f.Game)`"" | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Run-Case 'reparse-game-directory' {
            Expect-Refusal { Invoke-Patch $f.C $junction 'Install' } 'reparse'
            Assert-FixturePair $f 'original'
        }
    } else {
        $script:TestResults += @{ name = 'reparse-game-directory'; status = 'skip'; reason = 'junction privilege/filesystem unavailable' }
        Write-Warning 'Pominięto test przekierowania katalogu (SKIP reparse): brak uprawnień lub obsługi junction.'
    }
    $f = New-Fixture $root 'reparse-staging'
    $junction = Join-Path $f.Root 'staging-junction'
    & $env:ComSpec /d /c "mklink /J `"$junction`" `"$(Join-Path $f.C.Package 'GameFiles')`"" | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Run-Case 'reparse-staging-source' {
            Expect-Refusal { Replace-Verified $f.C (Join-Path $junction 'data.win') (Join-Path $f.Game 'data.win') $f.C.Original['data.win'] $f.C.Patched['data.win'] } 'reparse'
            Assert-FixturePair $f 'original'
        }
    } else { $script:TestResults += @{ name = 'reparse-staging-source'; status = 'skip'; reason = 'junction unavailable' } }
    $f = New-Fixture $root 'reparse-file'
    $target = Join-Path $f.Game 'data.win'
    $outside = Join-Path $f.Root 'outside-original'
    [IO.File]::Move($target, $outside)
    & $env:ComSpec /d /c "mklink `"$target`" `"$outside`"" | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Run-Case 'reparse-target-file' {
            Expect-Refusal { Invoke-Patch $f.C $f.Game 'Install' } 'reparse'
            Assert-Hash $outside $f.C.Original['data.win']
        }
    } else {
        [IO.File]::Move($outside, $target)
        $script:TestResults += @{ name = 'reparse-target-file'; status = 'skip'; reason = 'symlink privilege unavailable' }
        Write-Warning 'Pominięto test dowiązania pliku (SKIP reparse-target-file): brak uprawnień do utworzenia symlink.'
    }
    Run-Case 'preflight-read-only' {
        $f = New-Fixture $root 'preflight'
        Assert-Test ((Invoke-Patch $f.C $f.Game 'Preflight') -ceq 'preflight:original') 'preflight'
        Assert-Test (-not [IO.Directory]::Exists($f.C.State)) 'no state writes'
        Assert-FixturePair $f 'original'
    }
    Write-NewJson (Join-Path $root 'tests.json') @{ version = '1.0-beta'; tests = $script:TestResults; realGameStarted = $false; syntheticOnly = $true }
    Write-Host "Testy zakończone (SELFTEST OK): $($script:TestResults.Count) przypadków; raport $root\tests.json"
}
