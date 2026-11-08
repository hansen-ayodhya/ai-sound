@echo off
chcp 65001 >nul
echo Memasang yt-dlp dan ffmpeg untuk pengecekan siaran YouTube (butuh internet)...
winget install -e --id yt-dlp.yt-dlp
winget install -e --id Gyan.FFmpeg
echo.
echo Selesai. TUTUP lalu BUKA LAGI AI Sound Engineer supaya alatnya terbaca.
echo Untuk memperbarui nanti: winget upgrade yt-dlp.yt-dlp
pause
