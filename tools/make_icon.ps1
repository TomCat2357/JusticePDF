#requires -Version 5.1
<#
.SYNOPSIS
    Convert an image (PNG/JPG/...) into a multi-resolution Windows .ico,
    center-cropped to a square and resized to standard icon sizes.

.DESCRIPTION
    Produces a single .ico containing 16/32/48/64/128/256 px images (each stored
    as a PNG-compressed entry, valid on Vista+). Used to set the JusticePDF app
    icon -- write it to tools\justicepdf.ico and the other tools pick it up
    automatically (build_launcher_exe.ps1 embeds it, set_default_app.ps1 uses it
    as DefaultIcon, create_shortcut.ps1 uses it for the shortcut).

.PARAMETER Source
    Path to the source image.

.PARAMETER OutPath
    Output .ico path. Defaults to tools\justicepdf.ico next to this script.

.PARAMETER Sizes
    Icon sizes to include. Default 16,32,48,64,128,256.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\make_icon.ps1 -Source picture.png
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$Source,
    [string]$OutPath,
    [int[]]$Sizes = @(16, 32, 48, 64, 128, 256)
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

if (-not (Test-Path $Source)) { throw "Source image not found: $Source" }
$Source = (Resolve-Path -LiteralPath $Source).Path
if (-not $OutPath) { $OutPath = Join-Path $PSScriptRoot 'justicepdf.ico' }

# Load source and center-crop to the largest square it contains.
$img = [System.Drawing.Image]::FromFile($Source)
try {
    $side = [Math]::Min($img.Width, $img.Height)
    $sx   = [int](($img.Width  - $side) / 2)
    $sy   = [int](($img.Height - $side) / 2)
    $srcRect = New-Object System.Drawing.Rectangle($sx, $sy, $side, $side)

    # Render each size to a PNG byte[].
    $pngs = New-Object 'System.Collections.Generic.List[byte[]]'
    foreach ($s in ($Sizes | Sort-Object)) {
        $bmp = New-Object System.Drawing.Bitmap($s, $s, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $g   = [System.Drawing.Graphics]::FromImage($bmp)
        $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $g.PixelOffsetMode   = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
        $g.SmoothingMode     = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
        $g.Clear([System.Drawing.Color]::Transparent)
        $dst = New-Object System.Drawing.Rectangle(0, 0, $s, $s)
        $g.DrawImage($img, $dst, $srcRect, [System.Drawing.GraphicsUnit]::Pixel)
        $g.Dispose()
        $ms = New-Object System.IO.MemoryStream
        $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
        $pngs.Add($ms.ToArray())
        $ms.Dispose(); $bmp.Dispose()
    }
} finally {
    $img.Dispose()
}

# Assemble the ICO: ICONDIR (6) + N * ICONDIRENTRY (16) + concatenated PNGs.
$sorted = $Sizes | Sort-Object
$fs = [System.IO.File]::Open($OutPath, [System.IO.FileMode]::Create)
$bw = New-Object System.IO.BinaryWriter($fs)
try {
    $bw.Write([uint16]0)               # reserved
    $bw.Write([uint16]1)               # type: icon
    $bw.Write([uint16]$pngs.Count)     # image count

    $offset = 6 + (16 * $pngs.Count)
    for ($i = 0; $i -lt $pngs.Count; $i++) {
        $s   = $sorted[$i]
        $len = $pngs[$i].Length
        $dim = if ($s -ge 256) { 0 } else { $s }   # 0 means 256 in the spec
        $bw.Write([byte]$dim)          # width
        $bw.Write([byte]$dim)          # height
        $bw.Write([byte]0)             # palette count
        $bw.Write([byte]0)             # reserved
        $bw.Write([uint16]1)           # color planes
        $bw.Write([uint16]32)          # bits per pixel
        $bw.Write([uint32]$len)        # bytes of image data
        $bw.Write([uint32]$offset)     # offset of image data
        $offset += $len
    }
    foreach ($p in $pngs) { $bw.Write($p) }
} finally {
    $bw.Dispose(); $fs.Dispose()
}

Write-Host "Icon written : $OutPath  (sizes: $($sorted -join ', '))"
