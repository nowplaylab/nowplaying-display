@echo off
rem NowPlaying: install the newest version right now (queued drafts included)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update-now.ps1" %*
echo.
pause
