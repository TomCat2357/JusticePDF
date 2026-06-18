#requires -Version 5.1
<#
.SYNOPSIS
    Create a Desktop shortcut for JusticePDF with a generated "cool" icon and a
    Ctrl+Alt+J global hotkey. The shortcut is pinnable to the taskbar. Per-user,
    NO admin rights required.

.DESCRIPTION
    1. Ensures an icon exists. If tools\justicepdf.ico is missing (and no -Icon
       is given), this script GENERATES one: a navy->blue gradient rounded tile
       with a white "J" and a red "PDF" badge, written as a 256x256 PNG-in-ICO.
    2. Resolves the launch target -- pythonw.exe + tools\justicepdf_open.pyw.
    3. Creates "<Desktop>\JusticePDF.lnk" pointing at the target, with the icon
       and Hotkey = "Ctrl+Alt+J".

    A Desktop (or Start-Menu) .lnk is what makes the Ctrl+Alt+J hotkey work:
    Windows activates a shortcut's hotkey only from those locations.

    Pinning to the taskbar: Windows 10/11 blocks scripts from pinning silently
    (the pin verb is removed for automation). The created .lnk IS pinnable --
    right-click it -> "Pin to taskbar" (or "Show more options" first on Win11).
    Pass -StartMenu to also drop a copy in the Start Menu for easy pinning.

.PARAMETER Target
    Explicit launch target (.exe or pythonw.exe). Auto-detected when omitted.

.PARAMETER Icon
    Path to an .ico to use. Defaults to tools\justicepdf.ico, generating it if
    absent.

.PARAMETER Hotkey
    Shortcut hotkey. Default "Ctrl+Alt+J".

.PARAMETER Name
    Shortcut base name (no extension). Default "JusticePDF".

.PARAMETER StartMenu
    Also create the shortcut in the per-user Start Menu Programs folder.

.PARAMETER Force
    Overwrite an existing shortcut / regenerate the icon even if present.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\create_shortcut.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\create_shortcut.ps1 -StartMenu -Hotkey "Ctrl+Alt+J"
#>
[CmdletBinding()]
param(
    [string]$Target,
    [string]$Icon,
    [string]$Hotkey = 'Ctrl+Alt+J',
    [string]$Name = 'JusticePDF',
    [switch]$StartMenu,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$root  = Split-Path -Parent $PSScriptRoot          # app root (parent of tools\)
$tools = $PSScriptRoot

# --- Generate a cool icon (256x256 PNG wrapped in an ICO container) -----------
function New-JusticePdfIcon {
    param([Parameter(Mandatory)] [string]$Path)

    Add-Type -AssemblyName System.Drawing

    $size = 256
    $bmp  = New-Object System.Drawing.Bitmap($size, $size)
    $g    = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode     = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.InterpolationMode  = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.TextRenderingHint  = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
    $g.Clear([System.Drawing.Color]::Transparent)

    # Rounded-rectangle tile path.
    function New-RoundedRect {
        param([float]$x, [float]$y, [float]$w, [float]$h, [float]$r)
        $p = New-Object System.Drawing.Drawing2D.GraphicsPath
        $d = $r * 2
        $p.AddArc($x,         $y,         $d, $d, 180, 90)
        $p.AddArc($x + $w - $d, $y,         $d, $d, 270, 90)
        $p.AddArc($x + $w - $d, $y + $h - $d, $d, $d,   0, 90)
        $p.AddArc($x,         $y + $h - $d, $d, $d,  90, 90)
        $p.CloseFigure()
        return $p
    }

    $margin = 14
    $tile   = New-RoundedRect $margin $margin ($size - 2*$margin) ($size - 2*$margin) 44

    # Diagonal gradient: deep indigo -> royal blue.
    $rect = New-Object System.Drawing.Rectangle(0, 0, $size, $size)
    $c1   = [System.Drawing.Color]::FromArgb(255, 26, 35, 92)    # #1A235C indigo
    $c2   = [System.Drawing.Color]::FromArgb(255, 41, 98, 227)   # #2962E3 royal blue
    $grad = New-Object System.Drawing.Drawing2D.LinearGradientBrush($rect, $c1, $c2, 45.0)
    $g.FillPath($grad, $tile)

    # Soft top highlight for a glossy look.
    $hl = [System.Drawing.Color]::FromArgb(40, 255, 255, 255)
    $hlBrush = New-Object System.Drawing.SolidBrush($hl)
    $hlPath  = New-RoundedRect $margin $margin ($size - 2*$margin) (($size - 2*$margin) / 2) 44
    $g.FillPath($hlBrush, $hlPath)

    # Big white serif "J".
    $jFont  = New-Object System.Drawing.Font('Georgia', 150, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
    $white  = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::White)
    $fmt    = New-Object System.Drawing.StringFormat
    $fmt.Alignment     = [System.Drawing.StringAlignment]::Center
    $fmt.LineAlignment = [System.Drawing.StringAlignment]::Center
    $jRect  = New-Object System.Drawing.RectangleF(0, -18, $size, $size)
    $g.DrawString('J', $jFont, $white, $jRect, $fmt)

    # Red "PDF" badge at the bottom.
    $badgeW = 150; $badgeH = 56
    $badgeX = ($size - $badgeW) / 2
    $badgeY = $size - $margin - $badgeH - 10
    $badge  = New-RoundedRect $badgeX $badgeY $badgeW $badgeH 14
    $red    = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 214, 40, 40)) # #D62828
    $g.FillPath($red, $badge)
    $pdfFont = New-Object System.Drawing.Font('Arial', 34, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
    $badgeRect = New-Object System.Drawing.RectangleF($badgeX, $badgeY, $badgeW, $badgeH)
    $g.DrawString('PDF', $pdfFont, $white, $badgeRect, $fmt)

    $g.Dispose()

    # PNG bytes.
    $ms = New-Object System.IO.MemoryStream
    $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
    $png = $ms.ToArray()
    $ms.Dispose(); $bmp.Dispose()

    # Wrap the PNG in a single-image ICO (Vista+ supports PNG-compressed icons).
    $fs = [System.IO.File]::Open($Path, [System.IO.FileMode]::Create)
    $bw = New-Object System.IO.BinaryWriter($fs)
    try {
        $bw.Write([uint16]0)        # reserved
        $bw.Write([uint16]1)        # type: 1 = icon
        $bw.Write([uint16]1)        # image count
        $bw.Write([byte]0)          # width  (0 => 256)
        $bw.Write([byte]0)          # height (0 => 256)
        $bw.Write([byte]0)          # palette
        $bw.Write([byte]0)          # reserved
        $bw.Write([uint16]1)        # color planes
        $bw.Write([uint16]32)       # bits per pixel
        $bw.Write([uint32]$png.Length)  # size of image data
        $bw.Write([uint32]22)       # offset of image data (6 + 16)
        $bw.Write($png)
    } finally {
        $bw.Dispose(); $fs.Dispose()
    }
    Write-Host "Icon         : generated $Path"
}

# --- Resolve / generate the icon ---------------------------------------------
if (-not $Icon) { $Icon = Join-Path $tools 'justicepdf.ico' }
if ((Test-Path $Icon) -and -not $Force) {
    Write-Host "Icon         : using existing $Icon"
} else {
    try {
        New-JusticePdfIcon -Path $Icon
    } catch {
        Write-Host "Icon         : generation failed ($($_.Exception.Message)); shortcut will use the target's own icon" -ForegroundColor Yellow
        $Icon = $null
    }
}

# --- Resolve the launch target -----------------------------------------------
if ($Target) {
    if (-not (Test-Path $Target)) { throw "Specified -Target not found: $Target" }
    $Target = (Resolve-Path -LiteralPath $Target).Path
    $arguments = ''
} else {
    $launcher = Join-Path $root 'tools\justicepdf_open.pyw'
    if (-not (Test-Path $launcher)) { throw "Launcher not found: $launcher" }
    $candidates = @(
        '.venv\Scripts\pythonw.exe', 'venv\Scripts\pythonw.exe',
        'python\pythonw.exe', 'python\Scripts\pythonw.exe',
        'runtime\pythonw.exe', 'pythonw.exe',
        '..\..\.venv\Scripts\pythonw.exe', '..\..\venv\Scripts\pythonw.exe',
        '..\..\python\pythonw.exe', '..\..\python\Scripts\pythonw.exe',
        '..\..\runtime\pythonw.exe'
    ) | ForEach-Object { Join-Path $root $_ }
    $pythonw = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $pythonw) {
        throw "pythonw.exe not found under $root.`nPass it explicitly: -Target <path>\pythonw.exe"
    }
    $Target    = (Resolve-Path -LiteralPath $pythonw).Path
    $arguments = '"' + (Resolve-Path -LiteralPath $launcher).Path + '"'
}
Write-Host "Target       : $Target"
if ($arguments) { Write-Host "Arguments    : $arguments" }

# --- Create the shortcut(s) --------------------------------------------------
function New-Shortcut {
    param([Parameter(Mandatory)] [string]$LinkPath)

    if ((Test-Path $LinkPath) -and -not $Force) {
        Write-Host "Shortcut     : already exists, overwriting $LinkPath"
    }
    $shell = New-Object -ComObject WScript.Shell
    $sc = $shell.CreateShortcut($LinkPath)
    $sc.TargetPath       = $Target
    if ($arguments) { $sc.Arguments = $arguments }
    $sc.WorkingDirectory = $root
    $sc.Description       = 'JusticePDF'
    $sc.WindowStyle       = 1
    if ($Icon) { $sc.IconLocation = "$Icon,0" }
    if ($Hotkey) { $sc.Hotkey = $Hotkey }
    $sc.Save()
    Write-Host "Shortcut     : created $LinkPath  (Hotkey: $Hotkey)"
}

$desktop = [Environment]::GetFolderPath('Desktop')
New-Shortcut -LinkPath (Join-Path $desktop "$Name.lnk")

if ($StartMenu) {
    $programs = [Environment]::GetFolderPath('Programs')
    if (-not (Test-Path $programs)) { New-Item -ItemType Directory -Path $programs -Force | Out-Null }
    New-Shortcut -LinkPath (Join-Path $programs "$Name.lnk")
}

Write-Host ''
Write-Host 'Done.'
Write-Host "Launch with the icon on your Desktop, or press $Hotkey anywhere."
Write-Host 'To put it on the taskbar: right-click the Desktop shortcut ->'
Write-Host '  "Pin to taskbar"  (on Windows 11, via "Show more options" first).'
Write-Host '  (Windows blocks scripts from pinning silently, so this one step is manual.)'
