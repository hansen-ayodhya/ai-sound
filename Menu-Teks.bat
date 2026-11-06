@echo off
REM Cadangan: menu teks lama (tanpa browser).
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
set "PY=python"
where python >nul 2>nul || set "PY=py"
%PY% -m aise.menu
if errorlevel 1 pause
