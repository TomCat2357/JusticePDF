Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$DESIRED_PYTHON_VERSION = "3.13.11"   # user intent
$DESIRED_SERIES = "3.13."            # fallback series

$PYENV_ROOT = Join-Path $env:USERPROFILE ".pyenv"
$PYENV_HOME = Join-Path $PYENV_ROOT "pyenv-win"   # clone target folder

function Write-Step([string]$msg) { Write-Host $msg }

function Add-ToUserPath([string]$Dir) {
  if (-not (Test-Path $Dir)) { return }
  $dirNorm = $Dir.TrimEnd('\\')

  $current = [Environment]::GetEnvironmentVariable("Path","User")
  if (-not $current) { $current = "" }

  $parts = $current -split ';' | Where-Object { $_ -and $_.Trim() -ne "" }
  $exists = $false
  foreach ($p in $parts) {
    if ($p.TrimEnd('\\') -ieq $dirNorm) { $exists = $true; break }
  }

  if (-not $exists) {
    $new = ($parts + $dirNorm) -join ';'
    [Environment]::SetEnvironmentVariable("Path", $new, "User")
    Write-Host "User PATH added: $dirNorm"
  }

  $sessParts = $env:Path -split ';' | ForEach-Object { $_.TrimEnd('\\') }
  if (-not ($sessParts | Where-Object { $_ -ieq $dirNorm })) {
    $env:Path = "$env:Path;$dirNorm"
    Write-Host "Session PATH added: $dirNorm"
  }
}

function Set-UserEnv([string]$Name, [string]$Value) {
  $cur = [Environment]::GetEnvironmentVariable($Name, "User")
  if ($cur -ne $Value) {
    [Environment]::SetEnvironmentVariable($Name, $Value, "User")
    Write-Host "User ENV set: $Name=$Value"
  }
  # reflect in current session (dynamic env var name)
  Set-Item -Path ("Env:\" + $Name) -Value $Value
}

function Remove-IfExists([string]$Path) { if (Test-Path $Path) { Remove-Item $Path -Recurse -Force } }

function Ensure-GitOrScoopGit {
  $git = Get-Command git -ErrorAction SilentlyContinue
  if ($git) { return }

  $scoop = Get-Command scoop -ErrorAction SilentlyContinue
  if ($scoop) {
    Write-Host "Installing git via Scoop..."
    scoop install git | Out-Host
    return
  }

  throw "git is required but not found. Please install Git for Windows (or Scoop) and re-run."
}

function Find-PyenvBat([string]$BaseDir) {
  $hit = Get-ChildItem -Path $BaseDir -Recurse -Filter "pyenv.bat" -File -ErrorAction SilentlyContinue |
         Where-Object { $_.FullName -match "\\\\pyenv-win\\\\bin\\\\pyenv\\.bat$" } |
         Select-Object -First 1
  if ($hit) { return $hit.FullName }

  $hit2 = Get-ChildItem -Path $BaseDir -Recurse -Filter "pyenv.bat" -File -ErrorAction SilentlyContinue |
          Select-Object -First 1
  if ($hit2) { return $hit2.FullName }

  return $null
}

function Ensure-PyenvWinLatest {
  Write-Step "[1/7] Installing/Repairing pyenv-win (official) ..."
  Ensure-GitOrScoopGit

  if (-not (Test-Path $PYENV_ROOT)) { New-Item -ItemType Directory -Path $PYENV_ROOT | Out-Null }

  if (Test-Path $PYENV_HOME) {
    if (Test-Path (Join-Path $PYENV_HOME ".git")) {
      Write-Host "Updating existing pyenv-win repo..."
      Push-Location $PYENV_HOME
      try {
        & git fetch --all --prune | Out-Host
        & git reset --hard origin/master | Out-Host
      } finally { Pop-Location }
    } else {
      Write-Warning "Existing $PYENV_HOME exists but is not a git repo. Reinstalling..."
      Remove-IfExists $PYENV_HOME
    }
  }

  if (-not (Test-Path $PYENV_HOME)) {
    Write-Host "Cloning pyenv-win..."
    Push-Location $PYENV_ROOT
    try { & git clone https://github.com/pyenv-win/pyenv-win.git pyenv-win | Out-Host }
    finally { Pop-Location }
  }

  $pyenvBat = Find-PyenvBat -BaseDir $PYENV_HOME
  if (-not $pyenvBat) { throw "pyenv.bat not found under: $PYENV_HOME" }

  $pyenvBin = Split-Path -Parent $pyenvBat
  $pyenvWin = Split-Path -Parent $pyenvBin
  $pyenvShims = Join-Path $pyenvWin "shims"
  if (-not (Test-Path $pyenvShims)) { New-Item -ItemType Directory -Path $pyenvShims | Out-Null }

  # Set recommended env vars (root fix)
  Set-UserEnv "PYENV" $pyenvBat
  Set-UserEnv "PYENV_ROOT" $pyenvWin
  Set-UserEnv "PYENV_HOME" $pyenvWin

  Add-ToUserPath $pyenvBin
  Add-ToUserPath $pyenvShims

  $v = (& $pyenvBat --version) 2>$null
  Write-Host "pyenv: $v"

  return @{
    PyenvBat = $pyenvBat
    PyenvBin = $pyenvBin
    PyenvWin = $pyenvWin
    PyenvShims = $pyenvShims
  }
}

function Get-AvailableVersions([string]$PyenvBat) {
  # Normalizes the list output to version tokens
  $raw = (& $PyenvBat install --list) 2>$null
  if (-not $raw) { return @() }

  $vers = @()
  foreach ($line in ($raw -split "`r?`n")) {
    $t = $line.Trim()
    if ($t -match "^\\d+\\.\\d+\\.\\d+$") { $vers += $t }
  }
  return $vers
}

function Select-PythonVersion([string]$PyenvBat) {
  $vers = Get-AvailableVersions -PyenvBat $PyenvBat
  if (-not $vers -or $vers.Count -eq 0) {
    throw "pyenv install --list returned no parsable versions."
  }

  if ($vers -contains $DESIRED_PYTHON_VERSION) {
    return $DESIRED_PYTHON_VERSION
  }

  # fallback: choose max version within 3.13.*
  $cands = $vers | Where-Object { $_.StartsWith($DESIRED_SERIES) }
  if (-not $cands -or $cands.Count -eq 0) {
    throw "Desired series '$DESIRED_SERIES' is not available in pyenv definitions. Please choose another version series."
  }

  # Parse semantic versions and choose the latest
  $parsed = $cands | ForEach-Object {
    $p = $_.Split('.')
    [PSCustomObject]@{ v=$_; major=[int]$p[0]; minor=[int]$p[1]; patch=[int]$p[2] }
  } | Sort-Object major, minor, patch -Descending

  return ($parsed | Select-Object -First 1).v
}

function Fix-PythonVersionFile([string]$ProjectDir, [string]$Version) {
  $pv = Join-Path $ProjectDir ".python-version"
  Set-Content -Path $pv -Value $Version -Encoding ASCII
}

function Ensure-PythonPinned([string]$ProjectDir, [string]$PyenvBat) {
  Write-Step "[2/7] Selecting installable Python version..."
  $chosen = Select-PythonVersion -PyenvBat $PyenvBat

  if ($chosen -ne $DESIRED_PYTHON_VERSION) {
    Write-Warning "Requested $DESIRED_PYTHON_VERSION is not available in pyenv definitions. Falling back to latest available $DESIRED_SERIES*: $chosen"
  } else {
    Write-Host "Using requested Python version: $chosen"
  }

  Write-Step "[3/7] Installing Python $chosen via pyenv-win..."
  Fix-PythonVersionFile -ProjectDir $ProjectDir -Version $chosen

  $installed = (& $PyenvBat versions --bare) 2>$null
  if (-not ($installed -contains $chosen)) {
    & $PyenvBat install $chosen | Out-Host
  } else {
    Write-Host "Python $chosen already installed."
  }

  Push-Location $ProjectDir
  try {
    & $PyenvBat local $chosen | Out-Null
    & $PyenvBat rehash | Out-Null
  } finally { Pop-Location }

  $pyPath = (& $PyenvBat which python) 2>&1
  if (-not $pyPath -or ($pyPath -notmatch "python\\.exe$") -or -not (Test-Path $pyPath)) {
    throw "pyenv which python failed. Output: $pyPath"
  }

  $ver = & $pyPath --version 2>&1
  if ($ver -notmatch ("Python\\s+" + [regex]::Escape($chosen))) {
    throw "Resolved python is not $chosen. Got: $ver (`$pyPath=$pyPath)"
  }

  return @{ PythonPath = $pyPath; Version = $chosen }
}

function Remove-ExistingVenv([string]$ProjectDir) {
  $venv = Join-Path $ProjectDir ".venv"
  if (Test-Path $venv) {
    Write-Step "[4/7] Removing existing venv..."
    Remove-Item $venv -Recurse -Force
  }
}

function New-Venv([string]$ProjectDir, [string]$PythonPath) {
  $venvPath = Join-Path $ProjectDir ".venv"
  Write-Step "[5/7] Creating venv at $venvPath ..."
  & $PythonPath -m venv $venvPath
  $venvPython = Join-Path $venvPath "Scripts\\python.exe"
  if (-not (Test-Path $venvPython)) { throw "venv python not found: $venvPython" }
  return @{ VenvPath=$venvPath; VenvPython=$venvPython }
}

function Install-Project([string]$ProjectDir, [string]$VenvPython) {
  Write-Step "[6/7] Installing JusticePDF (editable) ..."
  & $VenvPython -m ensurepip --upgrade | Out-Host
#  & $VenvPython -m pip install --upgrade pip | Out-Host
  & $VenvPython -m pip install -e $ProjectDir | Out-Host
}

function Create-Shortcut([string]$ShortcutPath, [string]$ProjectDir) {
  $Pythonw = Join-Path $ProjectDir ".venv\\Scripts\\pythonw.exe"
  if (-not (Test-Path $Pythonw)) { Write-Warning "Shortcut skipped: pythonw.exe not found: $Pythonw"; return }
  $WshShell = New-Object -ComObject WScript.Shell
  $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
  $Shortcut.TargetPath = $Pythonw
  $Shortcut.Arguments = "-m src.main"
  $Shortcut.WorkingDirectory = $ProjectDir
  $Shortcut.IconLocation = $Pythonw
  $Shortcut.Save()
}

function Create-Shortcuts([string]$ProjectDir) {
  Write-Step "[7/7] Creating shortcuts..."
  $Desktop = [Environment]::GetFolderPath("Desktop")
  Create-Shortcut -ShortcutPath (Join-Path $Desktop "JusticePDF.lnk") -ProjectDir $ProjectDir
  Create-Shortcut -ShortcutPath (Join-Path $ProjectDir "JusticePDF.lnk") -ProjectDir $ProjectDir
}

# -------------------------
# Main
# -------------------------

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Step "=== Root-fix install: pyenv-win official + Python $DESIRED_PYTHON_VERSION (fallback to latest $DESIRED_SERIES*) + venv + JusticePDF ==="
Write-Host "ProjectDir: $ProjectDir"
Write-Host "PYENV_HOME: $PYENV_HOME"

$pyenvInfo = Ensure-PyenvWinLatest

Remove-ExistingVenv -ProjectDir $ProjectDir
$pinned = Ensure-PythonPinned -ProjectDir $ProjectDir -PyenvBat $pyenvInfo.PyenvBat

$venvInfo = New-Venv -ProjectDir $ProjectDir -PythonPath $pinned.PythonPath
Install-Project -ProjectDir $ProjectDir -VenvPython $venvInfo.VenvPython
Create-Shortcuts -ProjectDir $ProjectDir

Write-Host ""
Write-Host "=== Done ==="
Write-Host "Pinned:"
Write-Host "  Python requested: $DESIRED_PYTHON_VERSION"
Write-Host "  Python installed: $($pinned.Version)"
Write-Host "  pyenv          : $($pyenvInfo.PyenvBat)"
Write-Host "  venv           : $($venvInfo.VenvPath)"
Write-Host ""
Write-Host "Run:"
Write-Host "  $($venvInfo.VenvPython) -m src.main"
