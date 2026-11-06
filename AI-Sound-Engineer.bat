@echo off
REM Klik dua kali untuk membuka AI Sound Engineer di browser.
REM Semua fungsi hanya MEMBACA mixer.
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
set "PY=python"
where python >nul 2>nul || set "PY=py"
%PY% -m aise.server
if errorlevel 1 pause
