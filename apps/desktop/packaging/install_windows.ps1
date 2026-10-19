# Install the Orbital Agents desktop app for this Windows user:
#   - a virtualenv in apps\desktop\.venv
#   - a Start Menu shortcut "Orbital Agents"
#   - the orbital:// link handler (pairing links open the app), under HKCU only
# Usage (PowerShell):  powershell -ExecutionPolicy Bypass -File packaging\install_windows.ps1 [-NoUrlHandler]
param([switch]$NoUrlHandler)
$ErrorActionPreference = "Stop"

$Here = Split-Path -Parent $PSScriptRoot
$Py = if ($env:PYTHON) { $env:PYTHON } elseif (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }

& $Py -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if ($LASTEXITCODE -ne 0) { throw "Python 3.10+ is required (install it from python.org or the Microsoft Store)" }

& $Py -m venv "$Here\.venv"
& "$Here\.venv\Scripts\python.exe" -m pip install --upgrade pip | Out-Null
& "$Here\.venv\Scripts\python.exe" -m pip install -r "$Here\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

$Pythonw = "$Here\.venv\Scripts\pythonw.exe"
$Run = "$Here\run.py"

# Start Menu shortcut (pythonw: no console window)
$Programs = [Environment]::GetFolderPath("Programs")
$Shell = New-Object -ComObject WScript.Shell
$Lnk = $Shell.CreateShortcut("$Programs\Orbital Agents.lnk")
$Lnk.TargetPath = $Pythonw
$Lnk.Arguments = "`"$Run`""
$Lnk.WorkingDirectory = $Here
$Lnk.Description = "Machines, health and AI agent jobs on your Orbital Agents hub"
$Lnk.Save()

if (-not $NoUrlHandler) {
  $Key = "HKCU:\Software\Classes\orbital"
  New-Item -Path $Key -Force | Out-Null
  Set-ItemProperty -Path $Key -Name "(Default)" -Value "URL:Orbital Agents"
  Set-ItemProperty -Path $Key -Name "URL Protocol" -Value ""
  New-Item -Path "$Key\shell\open\command" -Force | Out-Null
  Set-ItemProperty -Path "$Key\shell\open\command" -Name "(Default)" -Value "`"$Pythonw`" `"$Run`" `"%1`""
}

Write-Host "Installed. Open 'Orbital Agents' from the Start Menu."
Write-Host "Edge WebView2 ships with Windows 10/11; if the window stays blank, install the WebView2 Runtime from Microsoft."
