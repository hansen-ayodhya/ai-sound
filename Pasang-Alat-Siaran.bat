@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo  Memasang alat pengecekan siaran YouTube (yt-dlp dan ffmpeg)
echo  Butuh internet. File disimpan di folder "tools" di sini.
echo  Simpan file ini di folder yang SAMA dengan AI-Sound-Engineer.exe
echo ============================================================
if not exist tools mkdir tools

echo.
echo [1/2] Mengunduh yt-dlp...
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $ProgressPreference='SilentlyContinue'; try { Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe' -OutFile 'tools\yt-dlp.exe'; Write-Host 'yt-dlp selesai.' } catch { Write-Host ('GAGAL: ' + $_.Exception.Message); exit 1 }"
if errorlevel 1 goto gagal

echo.
echo [2/2] Mengunduh ffmpeg (sekitar 100-150 MB, bisa beberapa menit)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $ProgressPreference='SilentlyContinue'; try { Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip' -OutFile 'tools\ffmpeg.zip'; if (Test-Path 'tools\ff_tmp') { Remove-Item -Recurse -Force 'tools\ff_tmp' }; Expand-Archive -Force 'tools\ffmpeg.zip' 'tools\ff_tmp'; $f = Get-ChildItem -Recurse 'tools\ff_tmp' -Filter ffmpeg.exe | Select-Object -First 1; Copy-Item $f.FullName 'tools\ffmpeg.exe' -Force; Remove-Item -Recurse -Force 'tools\ff_tmp'; Remove-Item -Force 'tools\ffmpeg.zip'; Write-Host 'ffmpeg selesai.' } catch { Write-Host ('GAGAL: ' + $_.Exception.Message); exit 1 }"
if errorlevel 1 goto gagal

echo.
echo ============================================================
echo  SELESAI. Isi folder tools:
dir /b tools
echo  Sekarang buka AI Sound Engineer lalu klik "Cek sekarang" lagi.
echo  (Tidak perlu memasang apa pun lagi atau menyalakan ulang komputer.)
echo  Untuk memperbarui nanti: jalankan file ini lagi.
echo ============================================================
pause
exit /b 0

:gagal
echo.
echo Pemasangan GAGAL. Cek koneksi internet lalu jalankan lagi.
echo Cara manual: unduh yt-dlp.exe dan ffmpeg.exe, lalu salin ke folder "tools" di samping AI-Sound-Engineer.exe.
pause
exit /b 1
