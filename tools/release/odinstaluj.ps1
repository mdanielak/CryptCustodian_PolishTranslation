#requires -Version 5.1
# Odinstalowanie korzysta ze wspólnego rdzenia i jego polskich komunikatów.
[CmdletBinding()]
param([string]$GamePath, [switch]$NonInteractive, [switch]$SelfTest)
$parameters = @{ Action = 'Uninstall'; NonInteractive = $NonInteractive; SelfTest = $SelfTest }
if ($GamePath) { $parameters.GamePath = $GamePath }
& (Join-Path $PSScriptRoot 'instaluj.ps1') @parameters
exit $LASTEXITCODE
