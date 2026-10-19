@echo off
rem Orbital Agents setup for Windows: runs setup.ps1 (double-click this, or: setup.cmd [--role satellite] [--yes] ...)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
if errorlevel 1 pause
