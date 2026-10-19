@echo off
rem orbital-track shim for Windows: runs orbital_track.py with the best Python available.
rem Put this folder on PATH, or copy this file next to a folder that is (it finds orbital_track.py by %~dp0).
setlocal
set "DIR=%~dp0"
if defined ORBITAL_PYTHON (
  "%ORBITAL_PYTHON%" "%DIR%orbital_track.py" %*
  exit /b %ERRORLEVEL%
)
if exist "%DIR%.venv\Scripts\python.exe" (
  "%DIR%.venv\Scripts\python.exe" "%DIR%orbital_track.py" %*
  exit /b %ERRORLEVEL%
)
if exist "%DIR%..\..\.venv\Scripts\python.exe" (
  "%DIR%..\..\.venv\Scripts\python.exe" "%DIR%orbital_track.py" %*
  exit /b %ERRORLEVEL%
)
where py >nul 2>nul && (
  py -3 "%DIR%orbital_track.py" %*
  exit /b %ERRORLEVEL%
)
where python >nul 2>nul && (
  python "%DIR%orbital_track.py" %*
  exit /b %ERRORLEVEL%
)
echo orbital-track: no Python 3 found (set ORBITAL_PYTHON) 1>&2
if /i "%~1"=="start" (echo untracked) else if /i "%~1"=="sub" (echo untracked) else if /i "%~1"=="add" (echo untracked) else (echo ok)
exit /b 0
