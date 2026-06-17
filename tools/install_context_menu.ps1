#requires -Version 5.1
<#
.SYNOPSIS
    Register the "JusticePDF de hiraku" (JusticePDFで開く) right-click context
    menu entry for PDF / Office / image files and folders. Per-user (HKCU)
    only -- NO admin rights required.

.DESCRIPTION
    Writes shell verbs under HKEY_CURRENT_USER so the current user can launch
    JusticePDF from Explorer's context menu:
      * .pdf files          -> import the PDF into the managed work folder
      * Office files         -> convert to PDF, then import
        (Word .doc/.docx/.docm, Excel .xls/.xlsx/.xlsm, PowerPoint .ppt/.pptx)
      * image files          -> convert to PDF, then import
        (.png/.jpg/.jpeg/.bmp/.tiff/.tif/.gif/.jp2/.jpx/.ppm/.pgm/.pbm/.pnm/.pam/.svg)
      * folders              -> copy the whole folder into the managed work folder
                                and open only that copy (no library window)

    This only ADDS a right-click verb; it never changes any default file
    association. To make JusticePDF a selectable "default app" (Open with ->
    Always), use tools\set_default_app.ps1 instead.

    The list of file extensions is read from src/utils/constants.py
    (IMPORT_EXTS, the single source of truth) so it never drifts from what the
    app can actually import. A static fallback list is used if python.exe
    cannot be located.

    The Japanese menu label is built from Unicode code points so this script
    stays pure-ASCII and is safe to run under Windows PowerShell 5.1 regardless
    of file encoding.

.PARAMETER Pythonw
    Path to the pythonw.exe that should run the app. If omitted, the script
    auto-detects it under the app root (handles a normal .venv checkout and a
    portable build that bundles Python, e.g. python\pythonw.exe).

.PARAMETER Background
    Also add the entry to the folder-background context menu (right-click on
    empty space inside a folder). Uses %V as the target path.

.PARAMETER ClassicMenu
    Also restore the Windows 11 classic (full) context menu so the entry shows
    at the top level instead of under "Show more options". Per-user, no admin.
    Requires an Explorer restart to take effect.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\install_context_menu.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\install_context_menu.ps1 -Background -ClassicMenu
#>
[CmdletBinding()]
param(
    [string]$Pythonw,
    [switch]$Background,
    [switch]$ClassicMenu
)

$ErrorActionPreference = 'Stop'

# --- Resolve paths -----------------------------------------------------------
$root     = Split-Path -Parent $PSScriptRoot          # app root (parent of tools\)
$launcher = Join-Path $root 'tools\justicepdf_open.pyw'

if (-not (Test-Path $launcher)) {
    throw "Launcher not found: $launcher"
}

# Locate pythonw.exe. Works both for a normal .venv checkout and for a portable
# build that bundles Python (relocated venv, embeddable runtime, etc.).
if ($Pythonw) {
    if (-not (Test-Path $Pythonw)) { throw "Specified -Pythonw not found: $Pythonw" }
    $pythonw = (Resolve-Path -LiteralPath $Pythonw).Path
} else {
    # Search both the app root (apps\JusticePDF) and the repository root two
    # levels up (..\..), where a portable build bundles Python (python\).
    $candidates = @(
        '.venv\Scripts\pythonw.exe',
        'venv\Scripts\pythonw.exe',
        'python\pythonw.exe',
        'python\Scripts\pythonw.exe',
        'runtime\pythonw.exe',
        'pythonw.exe',
        '..\..\.venv\Scripts\pythonw.exe',
        '..\..\venv\Scripts\pythonw.exe',
        '..\..\python\pythonw.exe',
        '..\..\python\Scripts\pythonw.exe',
        '..\..\runtime\pythonw.exe'
    ) | ForEach-Object { Join-Path $root $_ }
    $pythonw = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $pythonw) {
        # Last resort: bounded recursive search under the repo root (parent of
        # the app root) so a bundled python\ outside apps\JusticePDF is found.
        $searchRoot = Split-Path -Parent (Split-Path -Parent $root)
        if (-not $searchRoot) { $searchRoot = $root }
        $found = Get-ChildItem -Path $searchRoot -Filter pythonw.exe -Recurse -Depth 4 `
                    -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) { $pythonw = $found.FullName }
    }
    if (-not $pythonw) {
        throw "pythonw.exe not found under $root.`nPass it explicitly: -Pythonw <path>\pythonw.exe"
    }
    # Normalize (collapse any ..\ segments) so the registry command is clean.
    $pythonw = (Resolve-Path -LiteralPath $pythonw).Path
}
Write-Host "Using python : $pythonw"

# Menu label: "JusticePDF" + U+3067 U+958B U+304F  ("de hiraku")
$label   = 'JusticePDF' + [char]0x3067 + [char]0x958B + [char]0x304F
$verb    = 'JusticePDFOpen'

# Command line stored in the registry (paths + %1 each individually quoted).
$cmdFile = '"' + $pythonw + '" "' + $launcher + '" "%1"'
$cmdDir  = $cmdFile
$cmdBg   = '"' + $pythonw + '" "' + $launcher + '" "%V"'

# --- Resolve the importable file extensions ----------------------------------
# Single source of truth: IMPORT_EXTS in src/utils/constants.py. Query it via
# python.exe (sibling of the pythonw.exe found above); fall back to a static
# list (kept in sync with constants.py) when python.exe is unavailable, e.g. a
# pythonw-only portable build.
function Get-ImportExtensions {
    param([Parameter(Mandatory)] [string]$Pythonw, [Parameter(Mandatory)] [string]$Root)

    $python = $Pythonw -replace 'pythonw\.exe$', 'python.exe'
    if (Test-Path $python) {
        try {
            # Use single-quoted python string literals + chr(10): PowerShell
            # strips embedded double quotes when building a native command line.
            $code = "import sys; sys.path.insert(0, r'$Root'); " +
                    'from src.utils.constants import IMPORT_EXTS; ' +
                    'print(chr(10).join(sorted(IMPORT_EXTS)))'
            $out = & $python -c $code 2>$null
            if ($LASTEXITCODE -eq 0 -and $out) {
                $list = @($out -split "`r?`n" | Where-Object { $_ -match '^\.\w+$' })
                if ($list.Count -gt 0) {
                    Write-Host "Extensions  : from constants.py ($($list.Count))"
                    return $list
                }
            }
        } catch { }
    }
    # Fallback -- keep in sync with IMPORT_EXTS in src/utils/constants.py.
    Write-Host 'Extensions  : static fallback (could not query constants.py)'
    return @(
        '.pdf',
        '.doc', '.docx', '.docm',
        '.xls', '.xlsx', '.xlsm',
        '.ppt', '.pptx',
        '.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.tif', '.gif',
        '.jp2', '.jpx', '.ppm', '.pgm', '.pbm', '.pnm', '.pam', '.svg'
    )
}

# --- Helper: create a shell verb under a given base key ----------------------
function New-ShellVerb {
    param(
        [Parameter(Mandatory)] [string]$BaseKey,   # e.g. HKCU:\Software\Classes\Directory\shell
        [Parameter(Mandatory)] [string]$Command
    )
    $verbKey = Join-Path $BaseKey $verb
    $cmdKey  = Join-Path $verbKey 'command'
    New-Item -Path $verbKey -Force | Out-Null
    Set-Item -Path $verbKey -Value $label          # (Default) = menu label
    New-Item -Path $cmdKey -Force | Out-Null
    Set-Item -Path $cmdKey -Value $Command         # (Default) = command line
    Write-Host "  registered: $verbKey"
}

Write-Host 'Registering JusticePDF context menu (HKCU, no admin needed)...'

# Importable files: add the verb per extension (PDF + Office + images) without
# changing any default file association.
$extensions = Get-ImportExtensions -Pythonw $pythonw -Root $root
foreach ($ext in $extensions) {
    New-ShellVerb -BaseKey "HKCU:\Software\Classes\SystemFileAssociations\$ext\shell" -Command $cmdFile
}

# Folders.
New-ShellVerb -BaseKey 'HKCU:\Software\Classes\Directory\shell' -Command $cmdDir

# Optional: folder background (empty area inside a folder).
if ($Background) {
    New-ShellVerb -BaseKey 'HKCU:\Software\Classes\Directory\Background\shell' -Command $cmdBg
}

# Optional: Windows 11 classic full context menu (per-user).
if ($ClassicMenu) {
    $clsid = 'HKCU:\Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}\InprocServer32'
    New-Item -Path $clsid -Force | Out-Null
    Set-Item -Path $clsid -Value ''                # empty default value enables it
    Write-Host '  classic context menu enabled (restart Explorer to apply):'
    Write-Host '    Stop-Process -Name explorer -Force   # Explorer auto-restarts'
}

Write-Host ''
Write-Host 'Done.'
Write-Host 'On Windows 11 the entry appears under "Show more options" (Shift+right-click)'
Write-Host 'unless you re-run with -ClassicMenu and restart Explorer.'
Write-Host 'To remove: tools\uninstall_context_menu.ps1'
