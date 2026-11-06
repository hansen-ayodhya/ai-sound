@echo off
REM Seperti AI-Sound-Engineer.bat, tetapi bisa dibuka dari HP/tablet/komputer lain
REM di jaringan gereja dengan PIN. Semua fungsi tetap hanya MEMBACA mixer.
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
set "PY=python"
where python >nul 2>nul || set "PY=py"
%PY% -m aise.server --lan
if errorlevel 1 pause
