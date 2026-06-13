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

$targets = @(
    "HKCU:\Software\Classes\SystemFileAssociations\.pdf\shell\$verb",
    "HKCU:\Software\Classes\Directory\shell\$verb",
    "HKCU:\Software\Classes\Directory\Background\shell\$verb"
)

Write-Host 'Removing JusticePDF context menu entries...'
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
