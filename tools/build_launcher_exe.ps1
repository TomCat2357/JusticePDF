#requires -Version 5.1
<#
.SYNOPSIS
    Build a small native launcher, JusticePDF.exe, so the app shows up as
    "JusticePDF" (with an icon) in Windows' "Open with" list and Default apps
    UI -- instead of "Python" / pythonw.exe.

.DESCRIPTION
    Compiles a tiny C# stub into a windowed executable using the in-box .NET
    Framework C# compiler that ships with every Windows 10/11 (no Visual
    Studio, no admin, no extra install). The resulting JusticePDF.exe:

      * locates pythonw.exe next to itself (a normal .venv checkout or a
        portable build that bundles Python, e.g. python\pythonw.exe), and
      * launches tools\justicepdf_open.pyw, forwarding any file/folder argument.

    Because the exe carries a version resource (FileDescription = "JusticePDF")
    and, optionally, an icon, Windows displays it as "JusticePDF" wherever an
    application name is shown. Point set_default_app.ps1 at it (it auto-detects
    JusticePDF.exe) to register the friendly default-app association.

    The exe is a build artifact and is NOT committed to the repo; run this on
    the target machine after install.

.PARAMETER OutputPath
    Where to write the exe. Defaults to <app-root>\JusticePDF.exe.

.PARAMETER Icon
    Path to an .ico embedded into the exe. If omitted, the script uses
    tools\justicepdf.ico when present; otherwise the exe gets the default icon.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\build_launcher_exe.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\build_launcher_exe.ps1 -Icon assets\app.ico
#>
[CmdletBinding()]
param(
    [string]$OutputPath,
    [string]$Icon
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot          # app root (parent of tools\)

if (-not $OutputPath) { $OutputPath = Join-Path $root 'JusticePDF.exe' }

# Resolve the icon (explicit -Icon, else tools\justicepdf.ico if present).
if (-not $Icon) {
    $defaultIco = Join-Path $PSScriptRoot 'justicepdf.ico'
    if (Test-Path $defaultIco) { $Icon = $defaultIco }
}
if ($Icon) {
    if (-not (Test-Path $Icon)) { throw "Specified -Icon not found: $Icon" }
    $Icon = (Resolve-Path -LiteralPath $Icon).Path
}

# --- C# launcher source ------------------------------------------------------
# Single-quoted here-string: no PowerShell interpolation, so the C# is verbatim.
$src = @'
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

[assembly: AssemblyTitle("JusticePDF")]
[assembly: AssemblyProduct("JusticePDF")]
[assembly: AssemblyCompany("JusticePDF")]
[assembly: AssemblyFileVersion("0.1.0.0")]
[assembly: AssemblyVersion("0.1.0.0")]

namespace JusticePDFLauncher
{
    static class Program
    {
        // Mirror of the pythonw.exe search order used by the PowerShell tools.
        static readonly string[] PythonwCandidates = new string[] {
            @".venv\Scripts\pythonw.exe",
            @"venv\Scripts\pythonw.exe",
            @"python\pythonw.exe",
            @"python\Scripts\pythonw.exe",
            @"runtime\pythonw.exe",
            @"pythonw.exe",
            @"..\..\.venv\Scripts\pythonw.exe",
            @"..\..\venv\Scripts\pythonw.exe",
            @"..\..\python\pythonw.exe",
            @"..\..\python\Scripts\pythonw.exe",
            @"..\..\runtime\pythonw.exe"
        };

        [STAThread]
        static int Main(string[] args)
        {
            string root = AppDomain.CurrentDomain.BaseDirectory;

            string pythonw = FindFirst(root, PythonwCandidates);
            if (pythonw == null)
            {
                Fail("Could not find pythonw.exe near:\n" + root +
                     "\n\nExpected a .venv or a bundled python\\ folder.");
                return 1;
            }

            string launcher = Path.Combine(root, @"tools\justicepdf_open.pyw");
            if (!File.Exists(launcher))
            {
                Fail("Launcher not found:\n" + launcher);
                return 1;
            }

            StringBuilder sb = new StringBuilder();
            sb.Append('"').Append(launcher).Append('"');
            foreach (string a in args)
            {
                sb.Append(' ').Append('"').Append(a).Append('"');
            }

            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = pythonw;
            psi.Arguments = sb.ToString();
            psi.UseShellExecute = false;
            psi.WorkingDirectory = root;
            try
            {
                Process.Start(psi);
            }
            catch (Exception ex)
            {
                Fail("Failed to start JusticePDF:\n" + ex.Message);
                return 1;
            }
            return 0;
        }

        static string FindFirst(string root, string[] candidates)
        {
            foreach (string c in candidates)
            {
                try
                {
                    string p = Path.GetFullPath(Path.Combine(root, c));
                    if (File.Exists(p)) return p;
                }
                catch { }
            }
            return null;
        }

        static void Fail(string message)
        {
            MessageBox.Show(message, "JusticePDF",
                MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }
}
'@

# --- Compile with the in-box .NET Framework C# compiler -----------------------
$provider = New-Object Microsoft.CSharp.CSharpCodeProvider
$cp = New-Object System.CodeDom.Compiler.CompilerParameters
$cp.GenerateExecutable = $true
$cp.GenerateInMemory   = $false
$cp.OutputAssembly     = $OutputPath
$cp.ReferencedAssemblies.Add('System.dll')               | Out-Null
$cp.ReferencedAssemblies.Add('System.Windows.Forms.dll') | Out-Null

# /target:winexe => no console window; /win32icon embeds the app icon.
$opts = '/target:winexe'
if ($Icon) { $opts += ' /win32icon:"' + $Icon + '"' }
$cp.CompilerOptions = $opts

Write-Host "Compiling   : $OutputPath"
if ($Icon) { Write-Host "Icon        : $Icon" }

$result = $provider.CompileAssemblyFromSource($cp, $src)
if ($result.Errors.HasErrors) {
    Write-Host 'Build FAILED:' -ForegroundColor Red
    foreach ($e in $result.Errors) { Write-Host "  $($e.ErrorText)" }
    throw 'Failed to compile JusticePDF.exe'
}

Write-Host ''
Write-Host "Done. Built: $OutputPath"
Write-Host 'Next: register it as a default-app candidate:'
Write-Host '  powershell -ExecutionPolicy Bypass -File tools\set_default_app.ps1'
