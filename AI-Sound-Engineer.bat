@echo off
REM Klik dua kali file ini untuk membuka menu AI Sound Engineer.
REM Semua pilihan di menu hanya MEMBACA mixer.
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
set "PY=python"
where python >nul 2>nul || set "PY=py"
%PY% -m aise.menu
if errorlevel 1 pause
