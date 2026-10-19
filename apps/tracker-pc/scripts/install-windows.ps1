<#
.SYNOPSIS
  Installs the Agent Satellite Tracker on Windows.

.DESCRIPTION
  Creates apps\tracker-pc\.venv, installs requirements.txt (pywebview + Pillow; WebView2 comes with
  Windows 10/11), adds this folder to your user PATH so agents find orbital-track.cmd, writes the
  install marker the satellite reports, and starts the tracker at sign-in.

    powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1                  # Startup-folder shortcut
    powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1 -Autostart Task  # Task Scheduler instead
    powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1 -Autostart None
    powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1 -Uninstall

.PARAMETER Python
  The Python 3.10+ to build the venv with (default: the "py -3" launcher, then python.exe).
#>
param(
  [ValidateSet("Startup", "Task", "None")] [string]$Autostart = "Startup",
  [string]$Python = "",
  [switch]$NoPath,
  [switch]$Start,
  [switch]$Uninstall
)
$ErrorActionPreference = "Stop"
$Dir = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Dir ".venv"
$Pythonw = Join-Path $Venv "Scripts\pythonw.exe"
$VenvPython = Join-Path $Venv "Scripts\python.exe"
$Script = Join-Path $Dir "tracker.pyw"
$Shortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "Agent Satellite Tracker.lnk"
$TaskName = "Orbital Agents - Agent Satellite Tracker"

function Remove-FromUserPath([string]$folder) {
  $p = [Environment]::GetEnvironmentVariable("Path", "User")
  if ($p) {
    $parts = $p.Split(";") | Where-Object { $_ -and ($_.TrimEnd("\") -ne $folder.TrimEnd("\")) }
    [Environment]::SetEnvironmentVariable("Path", ($parts -join ";"), "User")
  }
}

if ($Uninstall) {
  if (Test-Path $Shortcut) { Remove-Item $Shortcut }
  schtasks /Query /TN $TaskName 2>$null | Out-Null
  if ($LASTEXITCODE -eq 0) { schtasks /Delete /TN $TaskName /F | Out-Null }
  Remove-FromUserPath $Dir
  if (Test-Path $VenvPython) { & $VenvPython $Script --remove-marker }
  Write-Host "Removed autostart, the PATH entry and the install marker (data in %USERPROFILE%\.orbital-agents is kept)."
  exit 0
}

# 1. Python + venv + requirements
if (-not (Test-Path $VenvPython)) {
  if ($Python) { & $Python -m venv $Venv }
  elseif (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv $Venv }
  elseif (Get-Command python -ErrorAction SilentlyContinue) { & python -m venv $Venv }
  else { throw "Python 3.10+ not found. Install it from https://www.python.org/downloads/ (tick 'Add to PATH')." }
}
& $VenvPython -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if ($LASTEXITCODE -ne 0) { throw "Python 3.10+ is required." }
& $VenvPython -m pip install --quiet --upgrade pip
& $VenvPython -m pip install --quiet -r (Join-Path $Dir "requirements.txt")

# 2. orbital-track on PATH (orbital-track.cmd lives in this folder and finds .venv by itself)
if (-not $NoPath) {
  $p = [Environment]::GetEnvironmentVariable("Path", "User")
  if (-not ($p -and ($p.Split(";") | Where-Object { $_.TrimEnd("\") -eq $Dir.TrimEnd("\") }))) {
    $new = if ($p) { $p.TrimEnd(";") + ";" + $Dir } else { $Dir }
    [Environment]::SetEnvironmentVariable("Path", $new, "User")
    Write-Host "Added $Dir to your user PATH (open a new terminal to use orbital-track)."
  }
}

# 3. The marker the satellite reads for capabilities.tracker
& $VenvPython $Script --install-marker | Out-Null

# 4. Start at sign-in
switch ($Autostart) {
  "Startup" {
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($Shortcut)
    $lnk.TargetPath = $Pythonw
    $lnk.Arguments = "`"$Script`""
    $lnk.WorkingDirectory = $Dir
    $lnk.Description = "Orbital Agents - Agent Satellite Tracker"
    $lnk.Save()
    Write-Host "Autostart: $Shortcut"
  }
  "Task" {
    $action = New-ScheduledTaskAction -Execute $Pythonw -Argument "`"$Script`"" -WorkingDirectory $Dir
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
      -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
      -Principal $principal -Force | Out-Null
    Write-Host "Autostart: Task Scheduler task '$TaskName' (at sign-in)."
  }
  "None" { }
}

if ($Start) {
  Start-Process -FilePath $Pythonw -ArgumentList "`"$Script`"" -WorkingDirectory $Dir
  Write-Host "Started."
}
Write-Host "Installed. Check it in a new terminal:  orbital-track ping"
