#requires -Version 5.1
<#
.SYNOPSIS
    Remove the "JusticePDFで開く" right-click context menu entries created by
    install_context_menu.ps1. Per-user (HKCU) only -- NO admin rights required.

.PARAMETER ClassicMenu
    Also remove the Windows 11 classic-context-menu CLSID override (restoring
    the default Windows 11 streamlined menu). Requires an Explorer restart.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\uninstall_context_menu.ps1
#>
[CmdletBinding()]
param(
    [switch]$ClassicMenu
)

$ErrorActionPreference = 'Stop'
$verb = 'JusticePDFOpen'

Write-Host 'Removing JusticePDF context menu entries...'

# Per-extension verbs: enumerate every SystemFileAssociations\<ext> and drop the
# verb wherever it exists. This removes all registered file types (PDF + Office
# + images) regardless of which extension list was used at install time.
$sfaRoot = 'HKCU:\Software\Classes\SystemFileAssociations'
if (Test-Path $sfaRoot) {
    Get-ChildItem -Path $sfaRoot -ErrorAction SilentlyContinue | ForEach-Object {
        $key = Join-Path $_.PSPath "shell\$verb"
        if (Test-Path $key) {
            Remove-Item -Path $key -Recurse -Force
            Write-Host "  removed: $($_.PSChildName) -> $verb"
        }
    }
}

# Folder and folder-background verbs.
$targets = @(
    "HKCU:\Software\Classes\Directory\shell\$verb",
    "HKCU:\Software\Classes\Directory\Background\shell\$verb"
)
foreach ($key in $targets) {
    if (Test-Path $key) {
        Remove-Item -Path $key -Recurse -Force
        Write-Host "  removed: $key"
    }
}

if ($ClassicMenu) {
    $clsid = 'HKCU:\Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}'
    if (Test-Path $clsid) {
        Remove-Item -Path $clsid -Recurse -Force
        Write-Host "  removed classic-menu override: $clsid"
        Write-Host '  (restart Explorer to apply: Stop-Process -Name explorer -Force)'
    }
}

Write-Host 'Done.'
