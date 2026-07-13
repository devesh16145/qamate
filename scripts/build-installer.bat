@echo off
REM ============================================================================
REM  QAmate — build the Windows installer (NSIS EXE)
REM
REM  Usage:
REM    scripts\build-installer.bat            build installer (uses system Python
REM                                           fallback at first run)
REM    scripts\build-installer.bat --offline  also stage a bundled Python runtime
REM                                           for a clean-machine (no Python) install
REM
REM  Output: dist\QAmate-Setup-<version>.exe
REM ============================================================================
setlocal
cd /d "%~dp0.."

echo.
echo === QAmate installer build =================================================
echo.

echo [1/4] Installing Node dependencies (electron, electron-builder, icon tools)...
call npm install
if errorlevel 1 goto :fail

echo.
echo [2/4] Generating app icon (.ico) from the brand SVG...
call npm run icon
if errorlevel 1 (
  echo   WARNING: icon generation failed. Ensure assets\branding\qamate-mark.ico exists
  echo            or see docs\INSTALLER.md for a manual route. Continuing...
)

echo.
if /I "%~1"=="--offline" (
  echo [3/4] Staging bundled Python runtime for offline install...
  call npm run prepare:python
  if errorlevel 1 goto :fail
) else (
  echo [3/4] Skipping bundled Python ^(system-Python fallback^). Use --offline to bundle.
)

echo.
echo [4/4] Packaging with electron-builder...
call npm run dist
if errorlevel 1 goto :fail

echo.
echo === DONE ===================================================================
echo Installer written to: dist\
dir /b dist\*.exe
echo.
goto :eof

:fail
echo.
echo BUILD FAILED. See the output above.
exit /b 1
