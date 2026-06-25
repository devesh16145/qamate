@echo off
REM Remove config.json from ALL git history (secrets were committed in older revisions).
REM Run ONLY after rotating Jira/app passwords. Requires: pip install git-filter-repo
setlocal
cd /d "%~dp0.."

where git >nul 2>&1 || (echo git not found & exit /b 1)

echo Checking for git-filter-repo...
python -m git_filter_repo --version >nul 2>&1
if errorlevel 1 (
  echo Installing git-filter-repo...
  python -m pip install git-filter-repo || exit /b 1
)

echo.
echo WARNING: This rewrites git history. Rotate exposed tokens BEFORE force-pushing.
echo Press Ctrl+C to cancel, or
pause

python -m git_filter_repo --path config.json --invert-paths --force
if errorlevel 1 exit /b 1

echo.
echo Done. Remote was removed by filter-repo. Re-add origin, then:
echo   git remote add origin https://github.com/YOUR_ORG/qamate.git
echo   git push --force-with-lease origin main
echo See docs\PUBLISHING.md
exit /b 0
