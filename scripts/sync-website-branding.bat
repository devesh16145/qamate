@echo off
REM Copy canonical brand SVGs into website/ for Vercel (self-contained deploy root).
cd /d "%~dp0\.."
if not exist "website\assets\branding" mkdir "website\assets\branding"
copy /Y "assets\branding\*.svg" "website\assets\branding\"
echo Synced branding SVGs to website\assets\branding\
