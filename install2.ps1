#requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Script directory (PS 5.1)
$script:ScriptDir = $PSScriptRoot
if (-not $script:ScriptDir -or $script:ScriptDir.Trim() -eq "")
{
    if ($PSCommandPath)
    { $script:ScriptDir = Split-Path -Parent $PSCommandPath 
    }
}
if (-not $script:ScriptDir -or $script:ScriptDir.Trim() -eq "")
{
    throw "Could not determine script directory."
}

# =========================
# Config
# =========================
$ProjectName   = "JusticePDF"
$PythonVersion = "3.14"
$ScoopRoot     = Join-Path $env:USERPROFILE "scoop"

# =========================
# Helpers
# =========================
function Write-Section([string]$Title)
{
    Write-Host ""
    Write-Host ("=== " + $Title + " ===") -ForegroundColor Cyan
}
function Write-Info([string]$Msg)
{ Write-Host ("[INFO] " + $Msg) -ForegroundColor Gray 
}
function Write-Ok([string]$Msg)
{ Write-Host ("[OK]   " + $Msg) -ForegroundColor Green 
}
function Write-Warn([string]$Msg)
{ Write-Host ("[WARN] " + $Msg) -ForegroundColor Yellow 
}
function Write-Err([string]$Msg)
{ Write-Host ("[ERR]  " + $Msg) -ForegroundColor Red 
}

function Has-Command([string]$Name)
{
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Set-EnvVar([string]$Name, [string]$Value)
{
    Set-Item -Path ("Env:" + $Name) -Value $Value
}

function Refresh-ProcessPath
{
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine"); if ($null -eq $machine)
    { $machine = "" 
    }
    $user    = [Environment]::GetEnvironmentVariable("Path", "User");    if ($null -eq $user)
    { $user = "" 
    }
    Set-EnvVar "Path" ($machine + ";" + $user)
}

function Append-PathOnce([string]$Dir)
{
    if (-not $Dir)
    { return 
    }
    $cur = [Environment]::GetEnvironmentVariable("Path", "Process")
    if ($null -eq $cur)
    { $cur = "" 
    }
    $parts = $cur -split ';' | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" }
    if ($parts -notcontains $Dir)
    {
        Set-EnvVar "Path" (($parts + $Dir) -join ';')
    }
}

function Create-Shortcut([string]$ShortcutPath, [string]$TargetPath, [string]$Arguments, [string]$WorkingDir)
{
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $TargetPath
    $Shortcut.Arguments = $Arguments
    $Shortcut.WorkingDirectory = $WorkingDir
    $Shortcut.Save()
    [System.Runtime.Interopservices.Marshal]::ReleaseComObject($WshShell) | Out-Null
}

# =========================
# Scoop
# =========================
function Ensure-Scoop
{
    Write-Section "Ensure Scoop"

    if (Has-Command "scoop")
    {
        $cmd = Get-Command scoop -ErrorAction SilentlyContinue
        if ($cmd)
        { Write-Ok ("Scoop is already available: " + $cmd.Source) 
        }
        return
    }

    Write-Info "Scoop not found. Installing..."
    Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force

    # Ensure TLS 1.2
    try
    { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 
    } catch
    {
    }

    Invoke-Expression (Invoke-RestMethod -UseBasicParsing "https://get.scoop.sh")
    Refresh-ProcessPath

    if (-not (Has-Command "scoop"))
    {
        throw "Scoop install failed. Ensure PowerShell can run scripts and internet is available."
    }

    Write-Ok "Scoop installed."
}

function Ensure-ScoopBucket([string]$BucketName)
{
    if (-not $BucketName -or $BucketName.Trim() -eq "")
    { return 
    }

    # scoop bucket list はバージョンにより string ではなく PSCustomObject を返すことがある
    $raw = $null
    try
    { $raw = & scoop bucket list 2>$null 
    } catch
    { $raw = $null 
    }

    $buckets = @()
    if ($raw)
    {
        foreach ($b in $raw)
        {
            $s = if ($b -and $b.PSObject.Properties["Name"])
            { $b.Name 
            } else
            { [string]$b 
            }
            $s = $s.Trim()
            if ($s)
            { $buckets += $s 
            }
        }
    }

    if ($buckets -contains $BucketName)
    {
        Write-Ok ("Bucket exists: " + $BucketName)
        return
    }

    Write-Info ("Adding bucket: " + $BucketName)
    & scoop bucket add $BucketName | Out-Host
    Refresh-ProcessPath
    Write-Ok ("Bucket added: " + $BucketName)
}

function Ensure-ScoopApp([string]$AppName, [string]$BucketName = $null)
{
    if ($BucketName)
    { Ensure-ScoopBucket $BucketName 
    }

    # 文字列/オブジェクトどちらの出力でも成立するよう Out-String で判定
    $listText = ""
    try
    { $listText = (& scoop list 2>$null | Out-String) 
    } catch
    { $listText = "" 
    }

    if ($listText -match ("(?m)^\s*" + [Regex]::Escape($AppName) + "\s"))
    {
        Write-Ok ("Already installed: " + $AppName)
        return
    }

    Write-Info ("Installing: " + $AppName)
    & scoop install $AppName | Out-Host
    Refresh-ProcessPath
    Write-Ok ("Installed: " + $AppName)
}

# =========================
# Python
# =========================
function Ensure-Python([string]$Version)
{
    Write-Section ("Ensure Python " + $Version)
    $shimDir = Join-Path $ScoopRoot "shims"
    Refresh-ProcessPath
    Append-PathOnce $shimDir

    # Python 3.14 は versions バケットから python314 としてインストール
    Ensure-ScoopApp "python314" "versions"

    # python314 をデフォルトの python shim に設定
    Write-Info "Setting python314 as default python shim..."
    & scoop reset python314 2>$null | Out-Host
    Refresh-ProcessPath
    Append-PathOnce $shimDir

    if (-not (Has-Command "python"))
    {
        throw "python command not found after Scoop install."
    }

    # バージョン確認
    $ver = (& python --version 2>&1) | Out-String
    $ver = $ver.Trim()
    Write-Info ("Python version: " + $ver)

    if ($ver -notmatch ("Python\s+" + [Regex]::Escape($Version)))
    {
        Write-Warn ("Python version is not " + $Version + ".x (actual: " + $ver + ")")
    } else
    {
        Write-Ok ("Python " + $Version + ".x confirmed.")
    }

    Refresh-ProcessPath
    Append-PathOnce $shimDir
}

function Ensure-Venv([string]$VenvDir)
{
    Write-Section ("Ensure venv " + $VenvDir)

    $venvPath = Join-Path $script:ScriptDir $VenvDir
    if (-not (Test-Path -LiteralPath $venvPath))
    {
        Write-Info "Creating venv..."
        & python -m venv $venvPath | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "Failed to create venv: $venvPath" }
        Write-Ok "venv created."
    } else
    {
        Write-Ok "venv already exists."
    }

    $venvPython = Join-Path $venvPath "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython))
    {
        throw "venv python not found: $venvPython"
    }

    Write-Info "Upgrading pip..."
    & $venvPython -m pip install --upgrade pip | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Failed to upgrade pip in venv: $venvPython" }

    return $venvPython
}

function Install-ProjectDeps([string]$VenvPython)
{
    Write-Section "Install project dependencies"

    $pyproject = Join-Path $script:ScriptDir "pyproject.toml"
    if (-not (Test-Path -LiteralPath $pyproject))
    {
        Write-Warn "pyproject.toml not found. Skipping dependency install."
        return
    }

    $tempRoot = $env:TEMP
    if (-not $tempRoot -or $tempRoot.Trim() -eq "")
    {
        $tempRoot = [System.IO.Path]::GetTempPath()
    }

    $pipTempDir = Join-Path $tempRoot "JusticePDF\pip"
    if (-not (Test-Path -LiteralPath $pipTempDir))
    {
        New-Item -Path $pipTempDir -ItemType Directory -Force | Out-Null
    }

    $oldTmp = [Environment]::GetEnvironmentVariable("TMP", "Process")
    $oldTemp = [Environment]::GetEnvironmentVariable("TEMP", "Process")

    # pyproject.toml の [project] dependencies には、個人情報検出エンジンが
    # 使う GiNZA/spaCy/Janome/RapidOCR も含まれる(すべて必須依存)。以前は
    # OCR(rapidocr/onnxruntime)だけ任意extra(`pip install -e .[ocr]`)扱いで、
    # このスクリプトがそれを指定していなかったため導入されない不具合が
    # あったが、必須依存化した現在は `pip install -e .` だけで全て入る。
    Write-Info "Installing project (editable) and deps from pyproject.toml..."
    Write-Info ("Temporary pip dir: " + $pipTempDir)
    try
    {
        Set-EnvVar "TMP" $pipTempDir
        Set-EnvVar "TEMP" $pipTempDir

        & $VenvPython -m pip install -e . | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "Failed to install project dependencies." }
    } finally
    {
        if ($null -eq $oldTmp)
        { Remove-Item -Path Env:TMP -ErrorAction SilentlyContinue
        } else
        { Set-EnvVar "TMP" $oldTmp
        }

        if ($null -eq $oldTemp)
        { Remove-Item -Path Env:TEMP -ErrorAction SilentlyContinue
        } else
        { Set-EnvVar "TEMP" $oldTemp
        }

        Remove-Item -LiteralPath $pipTempDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    Write-Ok "Dependencies installed."
}

function Test-OptionalPiiEngines([string]$VenvPython)
{
    # GiNZA(spaCy)・Janome・RapidOCRは個人情報検出の必須依存としてインストール
    # されるが、実際に import できるかは実行環境(Pythonのパッチバージョン、
    # 各ライブラリの相互バージョン等)に左右される。アプリ本体は
    # src/pii/engines.py の可用性チェックにより、importに失敗するエンジンを
    # 自動的に「未導入」としてグレーアウトし、そのエンジンの検出結果を
    # 0件として扱うだけでクラッシュしない設計になっているが、ここで
    # インストール直後に軽く動作確認しておくことで、ユーザーがアプリ内で
    # 突然「検出されない」と気付くより先に、インストール時点で警告できる。
    # (実例: Python 3.14 + pydantic の組み合わせでspaCyのimportが失敗する
    # 既知の非互換が2026-09時点で残っている。この場合GiNZAエンジンは
    # 検出設定ダイアログでグレーアウトされ、他のエンジン(正規表現/
    # SudachiPy/日時パターン/Janome)には影響しない)
    Write-Section "Verify optional PII detection engines"

    $checks = @(
        @{ Name = "GiNZA (spaCy)"; Code = "import spacy; spacy.load('ja_ginza')" },
        @{ Name = "Janome";        Code = "from janome.tokenizer import Tokenizer; Tokenizer()" },
        @{ Name = "RapidOCR";      Code = "import rapidocr" }
    )

    foreach ($check in $checks)
    {
        & $VenvPython -c $check.Code 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0)
        {
            Write-Ok ($check.Name + " OK")
        } else
        {
            $msg = $check.Name + " の読み込みに失敗しました。このエンジンは無効として扱われますが、アプリの起動自体は可能です。詳細はアプリ内の「個人情報検出の設定」>「検出エンジン」タブで確認してください。"
            Write-Warn $msg
        }
    }
}

# =========================
# Main
# =========================
try
{
    Write-Section ("Root-fix install: Scoop + Python " + $PythonVersion + " + venv + " + $ProjectName)

    Ensure-Scoop

    Write-Section "Install Git (Scoop)"
    Ensure-ScoopApp "git" "main"

    Ensure-Python $PythonVersion

    $venvPython = Ensure-Venv ".venv"
    Install-ProjectDeps $venvPython
    Test-OptionalPiiEngines $venvPython

    Write-Section "Create Shortcuts"
    $pythonwPath = Join-Path $script:ScriptDir ".venv\Scripts\pythonw.exe"
    $shortcutName = "JusticePDF.lnk"

    # プロジェクトフォルダにショートカットを作成
    $projectShortcut = Join-Path $script:ScriptDir $shortcutName
    Create-Shortcut $projectShortcut $pythonwPath "-m src.main" $script:ScriptDir
    Write-Ok ("Created: " + $projectShortcut)

    # デスクトップにショートカットを作成
    $desktopPath = [Environment]::GetFolderPath("Desktop")
    $desktopShortcut = Join-Path $desktopPath $shortcutName
    Create-Shortcut $desktopShortcut $pythonwPath "-m src.main" $script:ScriptDir
    Write-Ok ("Created: " + $desktopShortcut)

    Write-Section "Done"
    Write-Info ("venv python: " + $venvPython)
    Write-Info "Activate: .\.venv\Scripts\Activate.ps1"

} catch
{
    Write-Err $_.Exception.Message
    Write-Err $_.ScriptStackTrace
    exit 1
}
