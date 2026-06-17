#requires -Version 5.1
<#
.SYNOPSIS
    Register JusticePDF as a selectable "default app" (Open with -> Always)
    for PDF / Office / image files. Per-user (HKCU) only -- NO admin rights
    required.

.DESCRIPTION
    Windows 10/11 protects the per-extension default handler
    (HKCU\...\<ext>\UserChoice) with a hash, so a script CANNOT silently force
    itself to become the default app (third-party tools that forge the hash are
    unsupported by Microsoft and break across OS updates). This script does the
    supported part:

      1. Creates a shared ProgID "JusticePDF.AssocFile" under HKCU with an
         open command that launches the app (reusing tools/justicepdf_open.pyw).
      2. Adds that ProgID to each importable extension's OpenWithProgids list so
         JusticePDF appears under "Open with -> Choose another app".

    The USER then makes it the default by right-clicking a file ->
    "Open with" -> "Choose another app" -> JusticePDF -> tick
    "Always use this app to open .<ext> files".

    This is distinct from tools/install_context_menu.ps1, which only adds a
    right-click "JusticePDF de hiraku" verb and never touches associations.

    The list of file extensions is read from src/utils/constants.py
    (IMPORT_EXTS, the single source of truth) so it never drifts from what the
    app can actually import. A static fallback list is used if python.exe
    cannot be located.

    The script stays pure-ASCII so it is safe to run under Windows PowerShell
    5.1 regardless of file encoding.

.PARAMETER Pythonw
    Path to the pythonw.exe that should run the app. If omitted, the script
    auto-detects it under the app root (handles a normal .venv checkout and a
    portable build that bundles Python, e.g. python\pythonw.exe).

.PARAMETER Icon
    Path to an .ico file used as the DefaultIcon for the JusticePDF file type.
    If omitted, the script looks for tools\justicepdf.ico; if neither exists,
    no DefaultIcon is set (Windows shows a generic icon).

.PARAMETER Extensions
    Explicit list of extensions (e.g. '.pdf', '.png') to register. Defaults to
    IMPORT_EXTS from src/utils/constants.py.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\set_default_app.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\set_default_app.ps1 -Extensions '.pdf'
#>
[CmdletBinding()]
param(
    [string]$Pythonw,
    [string]$Icon,
    [string[]]$Extensions
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

# --- ProgID details ----------------------------------------------------------
$progId    = 'JusticePDF.AssocFile'
$progIdKey = "HKCU:\Software\Classes\$progId"
# Friendly file-type name: "JusticePDF" + U+6587 U+66F8 ("bunsho" = document)
$typeName  = 'JusticePDF ' + [char]0x6587 + [char]0x66F8

# Command line stored in the registry (paths + %1 each individually quoted).
$cmdOpen = '"' + $pythonw + '" "' + $launcher + '" "%1"'

# Resolve the icon (explicit -Icon, else tools\justicepdf.ico if present).
if (-not $Icon) {
    $defaultIco = Join-Path $PSScriptRoot 'justicepdf.ico'
    if (Test-Path $defaultIco) { $Icon = $defaultIco }
}
if ($Icon) {
    if (-not (Test-Path $Icon)) { throw "Specified -Icon not found: $Icon" }
    $Icon = (Resolve-Path -LiteralPath $Icon).Path
}

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

if ($Extensions -and $Extensions.Count -gt 0) {
    $extensions = @($Extensions | ForEach-Object { if ($_ -like '.*') { $_ } else { ".$_" } })
    Write-Host "Extensions  : from -Extensions ($($extensions.Count))"
} else {
    $extensions = Get-ImportExtensions -Pythonw $pythonw -Root $root
}

# --- Register the ProgID -----------------------------------------------------
Write-Host 'Registering JusticePDF as a default-app candidate (HKCU, no admin needed)...'

$cmdKey  = Join-Path $progIdKey 'shell\open\command'
New-Item -Path $progIdKey -Force | Out-Null
Set-Item -Path $progIdKey -Value $typeName            # (Default) = friendly type name
New-Item -Path $cmdKey -Force | Out-Null
Set-Item -Path $cmdKey -Value $cmdOpen                # (Default) = command line
if ($Icon) {
    $iconKey = Join-Path $progIdKey 'DefaultIcon'
    New-Item -Path $iconKey -Force | Out-Null
    Set-Item -Path $iconKey -Value ('"' + $Icon + '",0')
    Write-Host "  icon        : $Icon"
}
Write-Host "  registered  : $progIdKey"

# --- Offer the ProgID for each extension via OpenWithProgids -----------------
foreach ($ext in $extensions) {
    $owp = "HKCU:\Software\Classes\$ext\OpenWithProgids"
    New-Item -Path $owp -Force | Out-Null
    # Value name = ProgID, empty data (REG_SZ ""), per the OpenWithProgids spec.
    New-ItemProperty -Path $owp -Name $progId -Value '' -PropertyType String -Force | Out-Null
    Write-Host "  open-with   : $ext -> $progId"
}

Write-Host ''
Write-Host 'Done. JusticePDF is now offered as an "Open with" choice.'
Write-Host ''
Write-Host 'To make it the DEFAULT app (Windows protects this step, so it must be'
Write-Host 'done by you -- it cannot be forced silently by a script):'
Write-Host '  1. Right-click a .pdf file -> "Open with" -> "Choose another app".'
Write-Host '  2. Pick JusticePDF.'
Write-Host '  3. Tick "Always use this app to open .pdf files", then OK.'
Write-Host '  (Repeat per extension, or set it from Settings -> Default apps.)'
Write-Host ''
Write-Host 'To remove: tools\unset_default_app.ps1'
