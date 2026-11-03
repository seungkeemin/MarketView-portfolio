@echo off
REM MarketView launcher.
REM
REM Pure ASCII on purpose: cmd.exe reads batch files by byte offset, so any
REM non-ASCII byte here can be executed as a command. All logic and all Korean
REM text live in start.ps1 (UTF-8 with BOM).
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0start.ps1"
exit /b %errorlevel%
