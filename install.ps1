# ==========================================
# Scoop / Python (Standard) / venv
# Python is managed by Scoop (NOT uv)
# Always run in USER HOME
# PowerShell-safe / PATH-independent
# ==========================================

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Write-Host "=== Install Scoop, Python, venv, and JusticePDF ==="

# -------------------------------------------------
# [PRE] Save original directory, then move to user home
# -------------------------------------------------
$OriginalDir = Get-Location
Write-Host "[PRE] Original directory saved: $OriginalDir"
Write-Host "[PRE] Switching to user home directory..."
Set-Location $HOME
Write-Host "Current directory: $(Get-Location)"

# ----------------
# Scoop install
# ----------------
if (-not (Get-Command scoop -ErrorAction SilentlyContinue)) {
    Write-Host "[1/5] Installing Scoop..."
    Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned -Force
    Invoke-RestMethod -Uri https://get.scoop.sh | Invoke-Expression
} else {
    Write-Host "[1/5] Scoop already installed. Skipping."
}

scoop update
scoop install git

# ----------------
# Python install (via Scoop)
# ----------------
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "[2/5] Installing Python via Scoop..."
    scoop install python
} else {
    Write-Host "[2/5] Python already installed. Skipping."
}

# ----------------
# Create virtual environment (Standard venv)
# Under the directory where install.ps1 was executed
# ----------------
$VenvPath = Join-Path $OriginalDir ".venv"
$PythonwPath = Join-Path $VenvPath "Scripts\pythonw.exe"

Write-Host "[3/5] Creating virtual environment at $VenvPath ..."
python -m venv $VenvPath

# ----------------
# Activate virtual environment
# ----------------
Write-Host "[4/5] Activating virtual environment..."
. "$VenvPath\Scripts\Activate.ps1"

# ----------------
# ensurepip (INSIDE venv, PATH-independent)
# ----------------
Write-Host "[5/5] Running ensurepip in virtual environment..."
python -m ensurepip --upgrade

# ----------------
# Install JusticePDF dependencies (from pyproject.toml)
# ----------------
Write-Host "[EXTRA] Installing JusticePDF dependencies..."
python -m pip install --upgrade pip
python -m pip install -e $OriginalDir

# ----------------
# Verification (SAFE)
# ----------------
Write-Host ""
Write-Host "=== Verification ==="
python --version
python -m pip --version

Write-Host ""
Write-Host "VIRTUAL_ENV=$env:VIRTUAL_ENV"
Write-Host "Current directory: $(Get-Location)"

# ----------------
# Create run_justicepdf.ps1 wrapper script
# ----------------
Write-Host "[SHORTCUT 1/2] Creating run_justicepdf.ps1 wrapper script..."
$WrapperPath = Join-Path $OriginalDir "run_justicepdf.ps1"
$WrapperContent = @"
Set-Location -Path `"$OriginalDir`"
& `"$PythonwPath`" -m src.main
"@
Set-Content -Path $WrapperPath -Value $WrapperContent -Encoding UTF8
Write-Host "Wrapper script created: $WrapperPath"

# ----------------
# Create Desktop shortcut with Ctrl+Shift+J
# .lnk -> .ps1 -> .py の流れ
# ----------------
Write-Host "[SHORTCUT 2/2] Creating desktop shortcut with Ctrl+Shift+J..."
$WshShell = New-Object -ComObject WScript.Shell
$PowerShellPath = "powershell.exe"
$ShortcutPath = [System.IO.Path]::Combine([Environment]::GetFolderPath("Desktop"), "Run_JusticePDF.lnk")
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = $PowerShellPath
$Shortcut.Arguments = "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$WrapperPath`""
$Shortcut.WorkingDirectory = "$OriginalDir"
$Shortcut.Hotkey = "Ctrl+Shift+J"
$Shortcut.Save()
Write-Host "Shortcut created: $ShortcutPath"
Write-Host "Hotkey: Ctrl+Shift+J"

# Create same shortcut in install folder
$LocalShortcutPath = Join-Path $OriginalDir "Run_JusticePDF.lnk"
$LocalShortcut = $WshShell.CreateShortcut($LocalShortcutPath)
$LocalShortcut.TargetPath = $PowerShellPath
$LocalShortcut.Arguments = "-WindowStyle Hidden -ExecutionPolicy Bypass -File `"$WrapperPath`""
$LocalShortcut.WorkingDirectory = "$OriginalDir"
$LocalShortcut.Hotkey = "Ctrl+Shift+J"
$LocalShortcut.Save()
Write-Host "Shortcut created: $LocalShortcutPath"

Write-Host ""
Write-Host "[POST] Returning to original directory..."
Set-Location $OriginalDir
Write-Host "Current directory: $(Get-Location)"
Write-Host ""
Write-Host "All done."
