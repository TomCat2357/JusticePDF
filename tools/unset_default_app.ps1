#requires -Version 5.1
<#
.SYNOPSIS
    Remove the JusticePDF "default app" registration created by
    set_default_app.ps1. Per-user (HKCU) only -- NO admin rights required.

.DESCRIPTION
    Drops the "JusticePDF.AssocFile" ProgID and removes it from every
    extension's OpenWithProgids list.

    NOTE: If you already confirmed JusticePDF as the DEFAULT app for an
    extension ("Always use this app"), Windows stores that choice in a
    protected UserChoice key that a script cannot silently change. Pick a
    different default from Settings -> Default apps (or via "Open with ->
    Choose another app") for those extensions.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\unset_default_app.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$progId = 'JusticePDF.AssocFile'

Write-Host 'Removing JusticePDF default-app registration...'

# Remove the ProgID from every extension's OpenWithProgids list.
$classesRoot = 'HKCU:\Software\Classes'
if (Test-Path $classesRoot) {
    Get-ChildItem -Path $classesRoot -ErrorAction SilentlyContinue |
        Where-Object { $_.PSChildName -like '.*' } | ForEach-Object {
            $owp = Join-Path $_.PSPath 'OpenWithProgids'
            if (Test-Path $owp) {
                $prop = Get-ItemProperty -Path $owp -ErrorAction SilentlyContinue
                if ($prop -and ($prop.PSObject.Properties.Name -contains $progId)) {
                    Remove-ItemProperty -Path $owp -Name $progId -Force
                    Write-Host "  removed: $($_.PSChildName)\OpenWithProgids -> $progId"
                }
            }
        }
}

# Remove the ProgID itself.
$progIdKey = "HKCU:\Software\Classes\$progId"
if (Test-Path $progIdKey) {
    Remove-Item -Path $progIdKey -Recurse -Force
    Write-Host "  removed: $progIdKey"
}

Write-Host 'Done.'
Write-Host 'If JusticePDF was confirmed as the DEFAULT for some types, change those'
Write-Host 'from Settings -> Default apps (Windows protects that choice).'
