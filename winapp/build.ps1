<#
.SYNOPSIS
    Builds the Campus Captions Windows app and its installer.

.DESCRIPTION
    Two outputs, in winapp\dist:

        Campus Captions\                     the app (PyInstaller onedir)
        CampusCaptions-Setup-<version>.exe   the installer to hand out

    Run it from a checkout with the virtual environment already made:

        py -3 -m venv .venv
        .venv\Scripts\python.exe -m pip install -r requirements.txt `
            -r winapp\requirements-build.txt -r requirements-whisper.txt
        powershell -ExecutionPolicy Bypass -File winapp\build.ps1

    requirements-whisper.txt is included even though the source-tree README
    calls it optional: there, skipping it keeps a Deepgram-only install lean,
    but the packaged app offers Whisper as a clickable backend on the setup
    wizard and the status page, with no terminal to `pip install` from - if
    the wheel is not in this venv when PyInstaller runs, picking it there
    just silently never opens.

    The installer step needs Inno Setup 6 (https://jrsoftware.org/isdl.php).
    Without it the app is still built and this says so; pass -SkipInstaller
    to skip that step deliberately.

    Neither output is code-signed - there is no certificate in this setup -
    so the first person to run the installer on a given machine gets
    SmartScreen's "Windows protected your PC" prompt and has to click "More
    info" then "Run anyway". That is once per machine, not once per launch,
    and it is the same for every unsigned installer. A signing certificate is
    the only thing that removes it.
#>

[CmdletBinding()]
param(
    [switch]$SkipInstaller
)

$ErrorActionPreference = 'Stop'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$venvPython = Join-Path $root '.venv\Scripts\python.exe'

if (-not (Test-Path $venvPython)) {
    throw "No virtual environment at $venvPython - see the header of this script."
}

# One source of truth for the version; the .spec reads the same file.
$version = & $venvPython -c "import sys; sys.path.insert(0, r'$root'); from launcher.version import VERSION; print(VERSION)"
if ($LASTEXITCODE -ne 0) { throw 'Could not read launcher/version.py' }
$versionNumber = & $venvPython -c "import sys; sys.path.insert(0, r'$root'); from launcher.version import VERSION_NUMBER; print(VERSION_NUMBER)"
if ($LASTEXITCODE -ne 0) { throw 'Could not read launcher/version.py' }
Write-Host "--- Campus Captions $version ---" -ForegroundColor Cyan

Write-Host '--- running PyInstaller ---' -ForegroundColor Cyan
Push-Location $here
try {
    Remove-Item -Recurse -Force 'build', 'dist' -ErrorAction SilentlyContinue
    & $venvPython -m PyInstaller --noconfirm --clean 'CampusCaptions.spec'
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

    $app = Join-Path $here 'dist\Campus Captions\Campus Captions.exe'
    if (-not (Test-Path $app)) { throw "PyInstaller produced no $app" }
    Write-Host "built: $app" -ForegroundColor Green

    if ($SkipInstaller) {
        Write-Host 'skipping the installer (-SkipInstaller)' -ForegroundColor Yellow
        return
    }

    # Inno Setup does not put itself on PATH, so look where it installs.
    $iscc = Get-Command 'iscc.exe' -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty Source -First 1
    if (-not $iscc) {
        $iscc = @(
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
            "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
        ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    }
    if (-not $iscc) {
        Write-Warning @'
Inno Setup 6 was not found, so only the app folder was built.
Install it from https://jrsoftware.org/isdl.php and run this again to get
the single-file installer.
'@
        return
    }

    Write-Host '--- building the installer ---' -ForegroundColor Cyan
    & $iscc "/DAppVersion=$version" "/DAppVersionNumber=$versionNumber" 'installer.iss'
    if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed' }

    $setup = Join-Path $here "dist\CampusCaptions-Setup-$version.exe"
    Write-Host ''
    Write-Host "built: $setup" -ForegroundColor Green
    Write-Host 'This is the file to attach to a GitHub release.'
}
finally {
    Pop-Location
}
