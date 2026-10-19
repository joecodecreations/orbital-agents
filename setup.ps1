# Orbital Agents setup for Windows. Run from the repo:  .\setup.ps1  [--role hub|satellite] [--answers FILE] [--yes] [--dry-run]
# (or double-click setup.cmd). The same steps as ./setup on macOS and Linux:
#
# 1. finds Python 3.11+ (the py launcher, then python on PATH; offers `winget install Python.Python.3.13`)
# 2. creates or updates .venv and installs this package (pip install -e ".[dev]")
# 3. puts an `orbital` command in %USERPROFILE%\.local\bin (and adds that folder to your user PATH)
# 4. runs the setup wizard: `orbital setup <your arguments>`
#
# Safe to run again: it keeps .venv (reinstalling only when pyproject.toml changed) and your settings.
# AI agents: .\setup.ps1 --answers answers.toml --yes   (see config/answers.example.toml)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $Repo ".venv"
$VenvPython = Join-Path $Venv "Scripts\python.exe"
$VenvOrbital = Join-Path $Venv "Scripts\orbital.exe"
$BinDir = if ($env:ORBITAL_BIN_DIR) { $env:ORBITAL_BIN_DIR } else { Join-Path $env:USERPROFILE ".local\bin" }
$MinMinor = 11
$DryRun = $args -contains "--dry-run"
$Yes = ($args -contains "--yes") -or ($args -contains "-y") -or [Console]::IsInputRedirected

function Say($t) { Write-Host "  $t" }
function Ok($t) { Write-Host "  " -NoNewline; Write-Host "OK" -ForegroundColor Green -NoNewline; Write-Host " $t" }
function Warn($t) { Write-Host "  " -NoNewline; Write-Host "!" -ForegroundColor Yellow -NoNewline; Write-Host " $t" }
function Die($t) { Write-Host "  " -NoNewline; Write-Host "X" -ForegroundColor Red -NoNewline; Write-Host " $t"; exit 1 }
function Act([string]$exe, [string[]]$rest) {
    if ($DryRun) { Write-Host "  [dry-run] run: $exe $($rest -join ' ')" -ForegroundColor DarkGray; return }
    & $exe @rest
    if ($LASTEXITCODE -ne 0) { Die "$exe $($rest -join ' ') failed (exit $LASTEXITCODE)" }
}

if ($args -contains "-h" -or $args -contains "--help") {
    Get-Content $MyInvocation.MyCommand.Path | Select-Object -Skip 1 -First 9 | ForEach-Object { $_ -replace '^# ?', '' }
    if (Test-Path $VenvOrbital) { & $VenvOrbital setup --help }
    exit 0
}

Write-Host "Orbital Agents: preparing Python" -ForegroundColor White

# ---------------------------------------------------------------- 1. Python 3.11+
function Test-Python([string[]]$cmd) {
    try {
        $exe = $cmd[0]; $pre = @($cmd | Select-Object -Skip 1)
        & $exe @pre -c "import sys; sys.exit(0 if sys.version_info >= (3, $MinMinor) else 1)" 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}

function Find-Python {
    $candidates = @()
    if ($env:ORBITAL_PYTHON) { $candidates += ,@($env:ORBITAL_PYTHON) }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in "3.14", "3.13", "3.12", "3.11") { $candidates += ,@("py", "-$v") }
    }
    foreach ($name in "python3", "python") {
        $found = Get-Command $name -ErrorAction SilentlyContinue
        # Skip the Microsoft Store stub, which opens the Store instead of running.
        if ($found -and $found.Source -notlike "*WindowsApps*") { $candidates += ,@($found.Source) }
    }
    foreach ($c in $candidates) { if (Test-Python $c) { return ,$c } }
    return $null
}

$Py = $null
if ((Test-Path $VenvPython) -and (Test-Python @($VenvPython))) { $Py = @($VenvPython) } else { $Py = Find-Python }

if (-not $Py) {
    Warn "Python 3.$MinMinor or newer is needed and wasn't found."
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $answer = "y"
        if (-not $Yes) { $answer = Read-Host "  Install it now with winget (winget install Python.Python.3.13)? [Y/n]"; if (-not $answer) { $answer = "y" } }
        if ($answer -match '^(y|yes)$') {
            Act "winget" @("install", "--id", "Python.Python.3.13", "-e", "--accept-source-agreements", "--accept-package-agreements")
            $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
            $Py = Find-Python
        }
    } else {
        Say "Install it from https://www.python.org/downloads/windows/ (tick 'Add python.exe to PATH'), then run .\setup.ps1 again."
    }
    if (-not $Py -and $DryRun) { Say "[dry-run] would continue with the Python installed above"; exit 0 }
    if (-not $Py) { Die "no Python 3.$MinMinor+; nothing was changed." }
}
$PyExe = $Py[0]; $PyPre = @($Py | Select-Object -Skip 1)
$PyVersion = & $PyExe @PyPre -c "import platform; print(platform.python_version())"
Ok "Python: $($Py -join ' ') ($PyVersion)"

# ---------------------------------------------------------------- 2. .venv + package
$Stamp = Join-Path $Venv ".orbital-installed"
$sha = [System.Security.Cryptography.SHA256]::Create()
$text = (Get-Content (Join-Path $Repo "pyproject.toml") -Raw) + (& $PyExe @PyPre -c "import sys; print(sys.version)")
$WantStamp = ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($text))) -replace "-", "").ToLower()

if ($PyExe -ne $VenvPython) {
    if (Test-Path $Venv) {
        Warn ".venv exists but its Python is missing or too old; recreating it"
        if (-not $DryRun) { Remove-Item -Recurse -Force $Venv }
    }
    Act $PyExe ($PyPre + @("-m", "venv", $Venv))
    Ok "created .venv"
}

$current = if (Test-Path $Stamp) { (Get-Content $Stamp -Raw).Trim() } else { "" }
if ($current -ne $WantStamp -or -not (Test-Path $VenvOrbital)) {
    Say "installing packages into .venv (a minute the first time)..."
    Act $VenvPython @("-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--upgrade", "pip")
    Act $VenvPython @("-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-e", "$Repo[dev]")
    if (-not $DryRun) { Set-Content -Path $Stamp -Value $WantStamp -NoNewline }
    Ok "packages installed"
} else {
    Ok "packages up to date"
}

# ---------------------------------------------------------------- 3. orbital on PATH
$Shim = Join-Path $BinDir "orbital.cmd"
$ShimText = "@echo off`r`n`"$VenvOrbital`" %*`r`n"
if (-not $DryRun) {
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    if (-not (Test-Path $Shim) -or (Get-Content $Shim -Raw) -ne $ShimText) {
        Set-Content -Path $Shim -Value $ShimText -NoNewline
        Ok "made $Shim -> $VenvOrbital"
    } else {
        Ok "orbital is in $BinDir"
    }
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not (($userPath -split ";") -contains $BinDir)) {
        [Environment]::SetEnvironmentVariable("Path", ($userPath.TrimEnd(";") + ";" + $BinDir).TrimStart(";"), "User")
        Ok "added $BinDir to your user PATH (open a new terminal to use 'orbital')"
    }
} else {
    Write-Host "  [dry-run] write $Shim and add $BinDir to the user PATH" -ForegroundColor DarkGray
}

# ---------------------------------------------------------------- 4. the wizard
Write-Host ""
if (Test-Path $VenvOrbital) {
    & $VenvOrbital setup @args
    exit $LASTEXITCODE
}
if ($DryRun) { Say "[dry-run] would run: orbital setup $($args -join ' ')"; exit 0 }
Die "$VenvOrbital is missing after the install; see the pip output above"
