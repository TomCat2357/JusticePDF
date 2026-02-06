#requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Determine script directory reliably (works in PS 5.1)
# $PSScriptRoot is available in scripts; $PSCommandPath is also available.
$script:ScriptDir = $PSScriptRoot
if (-not $script:ScriptDir -or $script:ScriptDir.Trim() -eq "") {
  if ($PSCommandPath) {
    $script:ScriptDir = Split-Path -Parent $PSCommandPath
  }
}
if (-not $script:ScriptDir -or $script:ScriptDir.Trim() -eq "") {
  throw "Could not determine script directory (PSScriptRoot/PSCommandPath not available)."
}

function Write-Section([string]$Title) {
  Write-Host ""
  Write-Host ("=== " + $Title + " ===") -ForegroundColor Cyan
}

function Write-Info([string]$Msg) {
  Write-Host ("[INFO] " + $Msg)
}

function Write-Warn([string]$Msg) {
  Write-Host ("[WARN] " + $Msg) -ForegroundColor Yellow
}

function Write-Err([string]$Msg) {
  Write-Host ("[ERROR] " + $Msg) -ForegroundColor Red
}

function Resolve-RepoRoot {
  # Use the script directory computed at top-level (NOT $MyInvocation in a function)
  return (Resolve-Path $script:ScriptDir).Path
}

function Set-EnvVar([string]$Name, [string]$Value) {
  Set-Item -Path ("Env:" + $Name) -Value $Value
}

function Add-ToPath-User([string]$Dir) {
  if (-not (Test-Path $Dir)) { return }

  $current = [Environment]::GetEnvironmentVariable("Path", "User")
  if ($null -eq $current) { $current = "" }

  $parts = $current -split ";" | Where-Object { $_ -and $_.Trim() -ne "" }

  if ($parts -notcontains $Dir) {
    $new = ($parts + $Dir) -join ";"
    [Environment]::SetEnvironmentVariable("Path", $new, "User")
    Write-Info ("Added to User PATH: " + $Dir)
  } else {
    Write-Info ("Already in User PATH: " + $Dir)
  }
}

function Refresh-ProcessPath {
  $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
  $user    = [Environment]::GetEnvironmentVariable("Path", "User")
  if ($null -eq $machine) { $machine = "" }
  if ($null -eq $user) { $user = "" }
  Set-EnvVar "Path" ($machine + ";" + $user)
}

function Ensure-Command([string]$Name, [string]$Hint) {
  $cmd = Get-Command $Name -ErrorAction SilentlyContinue
  if (-not $cmd) { throw ("Command not found: " + $Name + ". " + $Hint) }
  return $cmd.Source
}

function Ensure-Scoop {
  Write-Section "Ensure Scoop"
  $scoop = Get-Command scoop -ErrorAction SilentlyContinue
  if ($scoop) {
    Write-Info ("Scoop available: " + $scoop.Source)
    return
  }

  Write-Warn "Scoop not found. Installing..."
  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

  $wc = New-Object System.Net.WebClient
  $scriptText = $wc.DownloadString("https://get.scoop.sh")
  Invoke-Expression $scriptText

  Refresh-ProcessPath
  $scoop2 = Get-Command scoop -ErrorAction SilentlyContinue
  if (-not $scoop2) { throw "Scoop install failed." }
  Write-Info ("Scoop installed: " + $scoop2.Source)
}

function Ensure-ScoopBucket([string]$Bucket) {
  if (-not $Bucket -or $Bucket.Trim() -eq "") { return }

  $bucketList = ""
  try { $bucketList = (& scoop bucket list 2>$null | Out-String) } catch { $bucketList = "" }

  if ($bucketList -notmatch [Regex]::Escape($Bucket)) {
    Write-Info ("Adding scoop bucket: " + $Bucket)
    & scoop bucket add $Bucket | Out-Host
  }
}

function Ensure-ScoopApp([string]$App, [string]$Bucket) {
  Ensure-ScoopBucket $Bucket

  $installed = $false
  try {
    $null = & scoop list $App 2>$null
    if ($LASTEXITCODE -eq 0) { $installed = $true }
  } catch {
    $installed = $false
  }

  if (-not $installed) {
    Write-Info ("Installing scoop app: " + $App)
    & scoop install $App | Out-Host
  } else {
    Write-Info ("Already installed: " + $App)
  }

  Refresh-ProcessPath
}

function Get-InstallInfo {
  $root = Resolve-RepoRoot
  $info = [PSCustomObject]@{
    RepoRoot = $root
    PyenvExe = $null
  }

  $pyenvCmd = Get-Command pyenv -ErrorAction SilentlyContinue
  if ($pyenvCmd) {
    $info.PyenvExe = $pyenvCmd.Source
  }

  return $info
}

function Ensure-PyenvWin {
  Write-Section "Ensure pyenv-win (via Scoop)"

  $installedSomething = $false

  try {
    Ensure-ScoopApp "pyenv-win" "main"
    $installedSomething = $true
  } catch {
    Write-Warn "scoop install pyenv-win failed. Trying 'pyenv'..."
  }

  if (-not $installedSomething) {
    try {
      Ensure-ScoopApp "pyenv" "main"
      $installedSomething = $true
    } catch {
      # continue; validate pyenv command next
    }
  }

  $info = Get-InstallInfo

  if (-not $info.PyenvExe -or -not (Test-Path $info.PyenvExe)) {
    $src = Ensure-Command "pyenv" "Check Scoop shims path and restart PowerShell."
    $info.PyenvExe = $src
  }

  Write-Info ("pyenv exe: " + $info.PyenvExe)
  return $info
}

function Ensure-Python([string]$PythonVersion) {
  Write-Section ("Ensure Python " + $PythonVersion)

  $info = Ensure-PyenvWin

  $scoopShims = Join-Path $env:USERPROFILE "scoop\shims"
  if (Test-Path $scoopShims) {
    Add-ToPath-User $scoopShims
    Refresh-ProcessPath
  }

  $versionsText = ""
  try { $versionsText = (& pyenv versions 2>$null | Out-String) } catch { $versionsText = "" }

  if ($versionsText -notmatch [Regex]::Escape($PythonVersion)) {
    Write-Info ("pyenv install " + $PythonVersion)
    & pyenv install $PythonVersion | Out-Host
  } else {
    Write-Info ("Python already installed in pyenv: " + $PythonVersion)
  }

  Push-Location $info.RepoRoot
  try {
    Write-Info ("pyenv local " + $PythonVersion)
    & pyenv local $PythonVersion | Out-Host
  } finally {
    Pop-Location
  }

  $py = Get-Command python -ErrorAction SilentlyContinue
  if (-not $py) { throw "python not found after pyenv local. Restart PowerShell and retry." }

  $ver = & python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
  Write-Info ("python resolved version: " + $ver)

  if ($ver -ne $PythonVersion) {
    Write-Warn ("Expected " + $PythonVersion + " but got " + $ver + ". Restart PowerShell and retry.")
  }

  return $info
}

function Ensure-Venv([string]$VenvDir) {
  Write-Section ("Ensure venv " + $VenvDir)

  $root = Resolve-RepoRoot
  $venvPath = Join-Path $root $VenvDir

  if (-not (Test-Path $venvPath)) {
    Write-Info ("Creating venv: " + $venvPath)
    & python -m venv $venvPath | Out-Host
  } else {
    Write-Info ("venv exists: " + $venvPath)
  }

  $pythonExe = Join-Path $venvPath "Scripts\python.exe"
  if (-not (Test-Path $pythonExe)) { throw ("venv python.exe not found: " + $pythonExe) }

  Write-Info "Upgrading pip/setuptools/wheel"
  & $pythonExe -m pip install -U pip setuptools wheel | Out-Host

  return $pythonExe
}

function Install-ProjectDeps([string]$VenvPython) {
  Write-Section "Install project dependencies"

  $root = Resolve-RepoRoot
  Push-Location $root
  try {
    $pyproject = Join-Path $root "pyproject.toml"
    $req = Join-Path $root "requirements.txt"

    if (Test-Path $pyproject) {
      Write-Info "pyproject.toml found -> pip install -e ."
      & $VenvPython -m pip install -e . | Out-Host
      return
    }

    if (Test-Path $req) {
      Write-Info "requirements.txt found -> pip install -r requirements.txt"
      & $VenvPython -m pip install -r $req | Out-Host
      return
    }

    Write-Warn "No pyproject.toml or requirements.txt found. Skipping dependency install."
  } finally {
    Pop-Location
  }
}

try {
  Write-Section "Root-fix install (Scoop + pyenv-win + Python 3.13.11 + venv)"

  $repoRoot = Resolve-RepoRoot
  Write-Info ("Repo root: " + $repoRoot)

  Ensure-Scoop

  Write-Section "Install Git (Scoop)"
  Ensure-ScoopApp "git" "main"

  Write-Section "Notes (optional Git registry/config)"
  Write-Host "If you want file associations/context menu for portable Git:"
  Write-Host "  - reg import C:\Users\<YOU>\scoop\apps\git\current\install-associations.reg"
  Write-Host "  - reg import C:\Users\<YOU>\scoop\apps\git\current\install-context.reg"
  Write-Host "If you want Git Credential Manager:"
  Write-Host "  - git config --global credential.helper manager"

  $pythonVersion = "3.13.11"
  $null = Ensure-Python $pythonVersion

  $venvPython = Ensure-Venv ".venv"
  Install-ProjectDeps $venvPython

  Write-Section "Done"
  Write-Info ("venv python: " + $venvPython)
  Write-Info "Activate: .\.venv\Scripts\Activate.ps1"

} catch {
  Write-Err $_.Exception.Message
  Write-Err $_.ScriptStackTrace
  exit 1
}
