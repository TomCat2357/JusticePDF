#requires -Version 5.1
<#
.SYNOPSIS
    Run the full JusticePDF Windows integration in one go: add the right-click
    context-menu verb and create a Desktop shortcut (cool icon + Ctrl+Alt+J
    hotkey). Per-user (HKCU) -- NO admin rights required.

.DESCRIPTION
    Wrapper that invokes the two setup scripts in the correct order:

      1. tools\install_context_menu.ps1 -> adds the "JusticePDFで開く" verb.
      2. tools\create_shortcut.ps1     -> Desktop shortcut with a generated icon
         and a Ctrl+Alt+J hotkey.

    Parameters are forwarded to the underlying scripts. Each step is reported;
    if a step fails the wrapper stops (set -ContinueOnError to keep going).

.PARAMETER Pythonw
    Path to pythonw.exe. Forwarded to install_context_menu.ps1. Auto-detected
    when omitted.

.PARAMETER Icon
    Path to an .ico. Forwarded to create_shortcut.ps1. Defaults to
    tools\justicepdf.ico (create_shortcut generates one there if it is missing).

.PARAMETER Hotkey
    Shortcut hotkey forwarded to create_shortcut.ps1. Default "Ctrl+Alt+J".

.PARAMETER StartMenu
    Forwarded to create_shortcut.ps1: also place a copy in the Start Menu.

.PARAMETER Background
    Forwarded to install_context_menu.ps1: also add the folder-background verb.

.PARAMETER ClassicMenu
    Forwarded to install_context_menu.ps1: restore the Win11 classic context menu.

.PARAMETER SkipShortcut
    Skip step 2 (do not create the Desktop shortcut).

.PARAMETER ContinueOnError
    Continue with the remaining steps even if one fails.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\setup_all.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\setup_all.ps1 -Background -ClassicMenu -StartMenu
#>
[CmdletBinding()]
param(
    [string]$Pythonw,
    [string]$Icon,
    [string]$Hotkey = 'Ctrl+Alt+J',
    [switch]$StartMenu,
    [switch]$Background,
    [switch]$ClassicMenu,
    [switch]$SkipShortcut,
    [switch]$ContinueOnError
)

$ErrorActionPreference = 'Stop'

$tools   = $PSScriptRoot
$context  = Join-Path $tools 'install_context_menu.ps1'
$shortcut = Join-Path $tools 'create_shortcut.ps1'

foreach ($s in @($context, $shortcut)) {
    if (-not (Test-Path $s)) { throw "Required script not found: $s" }
}

# Run one step, honoring -ContinueOnError. Returns $true on success.
function Invoke-Step {
    param(
        [Parameter(Mandatory)] [string]$Title,
        [Parameter(Mandatory)] [string]$Script,
        [hashtable]$Arguments = @{}
    )
    Write-Host ''
    Write-Host ('=' * 60)
    Write-Host "STEP: $Title"
    Write-Host ('=' * 60)
    try {
        & $Script @Arguments
        Write-Host "[OK] $Title" -ForegroundColor Green
        return $true
    } catch {
        Write-Host "[FAILED] $Title" -ForegroundColor Red
        Write-Host "  $($_.Exception.Message)" -ForegroundColor Red
        if (-not $ContinueOnError) {
            throw "Setup aborted at: $Title (use -ContinueOnError to skip past failures)"
        }
        return $false
    }
}

$ok = $true

# --- Step 1: install the context-menu verb -----------------------------------
$a = @{}
if ($Pythonw)     { $a['Pythonw']     = $Pythonw }
if ($Background)  { $a['Background']   = $true }
if ($ClassicMenu) { $a['ClassicMenu']  = $true }
$ok = (Invoke-Step -Title 'Install context menu' -Script $context -Arguments $a) -and $ok

# --- Step 2: create the Desktop shortcut (icon + Ctrl+Alt+J hotkey) ----------
if ($SkipShortcut) {
    Write-Host 'Skipping shortcut (-SkipShortcut).'
} else {
    $a = @{}
    if ($Icon)      { $a['Icon']    = $Icon }
    if ($Hotkey)    { $a['Hotkey']  = $Hotkey }
    if ($StartMenu) { $a['StartMenu'] = $true }
    $ok = (Invoke-Step -Title 'Create Desktop shortcut' -Script $shortcut -Arguments $a) -and $ok
}

Write-Host ''
Write-Host ('=' * 60)
if ($ok) {
    Write-Host 'All steps completed.' -ForegroundColor Green
} else {
    Write-Host 'Completed with one or more failed steps (see above).' -ForegroundColor Yellow
}
Write-Host ('=' * 60)
