# install.ps1
# JusticePDF installer (Windows / PowerShell)
# - Scoop (optional install)
# - Python (prefer Scoop Python; reject Microsoft Store alias python.exe)
# - Create venv
# - Install dependencies + editable install WITHOUT activation
# - Create Desktop shortcut
#
# NOTE:
# - Do NOT run `scoop update *` (updates all apps like nodejs-lts, and may break due to unrelated packages).
# - Update only Scoop itself + buckets, and (optionally) only the packages this installer cares about.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step([string]$msg) {
  Write-Host $msg
}

function Assert-NotWindowsAppsPython {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if (-not $cmd) { return }

  $src = $cmd.Source
  if ($src -and $src -like "*\AppData\Local\Microsoft\WindowsApps\python.exe") {
    Write-Host ""
    Write-Host "ERROR: 'python' points to Microsoft Store App Execution Alias:" -ForegroundColor Red
    Write-Host "  $src" -ForegroundColor Red
    Write-Host ""
    Write-Host "Fix (recommended):" -ForegroundColor Yellow
    Write-Host "  Settings -> Apps -> Advanced app settings -> App execution aliases"
    Write-Host "  Turn OFF: python.exe / python3.exe"
    Write-Host ""
    Write-Host "Then install real Python (recommended: Scoop):" -ForegroundColor Yellow
    Write-Host "  scoop install python"
    throw "Invalid python (WindowsApps alias)."
  }
}

function Ensure-Scoop {
  $scoop = Get-Command scoop -ErrorAction SilentlyContinue
  if ($scoop) {
    Write-Step "[1/5] Scoop already installed. Skipping."
    return
  }

  Write-Step "[1/5] Installing Scoop..."
  # Scoop install requires execution policy bypass (current process only)
  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force | Out-Null
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
  Invoke-Expression (Invoke-RestMethod -UseBasicParsing "https://get.scoop.sh")
}

function Update-ScoopMinimal {
  # Only update Scoop core + buckets.
  # Do NOT update all installed apps.
  Write-Host "Updating Scoop..."
  try {
    scoop update
    Write-Host "Updating buckets..."
    scoop bucket update
    Write-Host "Scoop core/buckets updated successfully!"
  } catch {
    Write-Warning "Scoop update failed. Continuing. Details: $($_.Exception.Message)"
  }
}

function Ensure-Git {
  # Git is often needed. Install if missing.
  $git = Get-Command git -ErrorAction SilentlyContinue
  if (-not $git) {
    Write-Host "Installing git (Scoop)..."
    try {
      scoop install git | Out-Host
    } catch {
      Write-Warning "Failed to install git via Scoop. Continuing. Details: $($_.Exception.Message)"
    }
    return
  }

  # If present, optionally update only git (won't touch nodejs-lts)
  try {
    $ver = (& git --version) -replace '^git version\s+',''
    Write-Host "WARN  'git' ($ver) is already installed."
    Write-Host "Use 'scoop update git' to install a new version."
  } catch {
    Write-Host "WARN  'git' is already installed."
    Write-Host "Use 'scoop update git' to install a new version."
  }
}

function Ensure-Python {
  # Reject Store alias first (if present)
  Assert-NotWindowsAppsPython

  $python = Get-Command python -ErrorAction SilentlyContinue
  if ($python) {
    # Try to confirm it is a real python
    try {
      $ver = & python --version 2>&1
      if ($ver -match "^Python\s+\d+\.\d+\.\d+") {
        Write-Step "[2/5] Python already installed. Skipping."
        return
      }
    } catch {
      # fall through to install
    }
  }

  Write-Step "[2/5] Installing Python (Scoop)..."
  scoop install python | Out-Host

  # Refresh PATH in current session (best effort)
  if (Get-Command refreshenv -ErrorAction SilentlyContinue) {
    refreshenv | Out-Null
  }

  # Re-check alias problem and python availability
  Assert-NotWindowsAppsPython

  $ver2 = & python --version 2>&1
  if ($ver2 -notmatch "^Python\s+\d+\.\d+\.\d+") {
    throw "Python installation did not result in a working python.exe. Output: $ver2"
  }
}

function New-Venv([string]$ProjectDir) {
  $venvPath = Join-Path $ProjectDir ".venv"
  Write-Step "[3/5] Creating virtual environment at $venvPath ..."

  # If venv exists, keep it by default. Uncomment next lines to recreate always.
  # if (Test-Path $venvPath) { Remove-Item -Recurse -Force $venvPath }

  & python -m venv $venvPath

  $venvPython = Join-Path $venvPath "Scripts\python.exe"
  if (-not (Test-Path $venvPython)) {
    throw "venv python not found: $venvPython"
  }

  return @{
    VenvPath   = $venvPath
    VenvPython = $venvPython
  }
}

function Install-Project([string]$ProjectDir, [string]$VenvPython) {
  Write-Step "[4/5] (Skip) Activating virtual environment..."
  $activate = Join-Path (Join-Path $ProjectDir ".venv") "Scripts\Activate.ps1"
  if (-not (Test-Path $activate)) {
    Write-Warning "Activate.ps1 not found (this is OK). Proceeding without activation (using venv python directly)."
  }

  Write-Step "[5/5] Installing JusticePDF..."
  & $VenvPython -m ensurepip --upgrade | Out-Host
  & $VenvPython -m pip install --upgrade pip | Out-Host

  # Editable install (pyproject.toml / setup.cfg whichever)
  & $VenvPython -m pip install -e $ProjectDir | Out-Host
}

function Create-DesktopShortcut([string]$ProjectDir) {
  $Desktop = [Environment]::GetFolderPath("Desktop")
  $ShortcutPath = Join-Path $Desktop "JusticePDF.lnk"
  $Pythonw = Join-Path $ProjectDir ".venv\Scripts\pythonw.exe"

  if (-not (Test-Path $Pythonw)) {
    Write-Warning "Shortcut skipped: pythonw.exe not found: $Pythonw"
    return
  }

  try {
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $Pythonw
    $Shortcut.Arguments = "-m src.main"
    $Shortcut.WorkingDirectory = $ProjectDir
    $Shortcut.IconLocation = $Pythonw
    $Shortcut.Save()
    Write-Host "Created shortcut: $ShortcutPath"
  } catch {
    Write-Warning "Failed to create shortcut. Details: $($_.Exception.Message)"
  }
}

# -------------------------
# Main
# -------------------------
$OriginalDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Step "=== Install Scoop, Python, venv, and JusticePDF ==="
Write-Step "[PRE] Original directory saved: $OriginalDir"
Write-Step "[PRE] Switching to user home directory..."
Set-Location $HOME
Write-Host "Current directory: $(Get-Location)"

Ensure-Scoop
Update-ScoopMinimal
Ensure-Git
Ensure-Python

# Work in project directory for venv + install
Set-Location $OriginalDir

$venvInfo = New-Venv -ProjectDir $OriginalDir
Install-Project -ProjectDir $OriginalDir -VenvPython $venvInfo.VenvPython

Write-Host ""
Write-Host "=== Done ==="
Write-Host "Run (without activation):"
Write-Host "  $($venvInfo.VenvPython) -m src.main"
Write-Host ""
Write-Host "Or activate manually if available:"
Write-Host "  .\ .venv\Scripts\Activate.ps1"

Create-DesktopShortcut -ProjectDir $OriginalDir
