@echo off
REM ─────────────────────────────────────────────────────────────────────────────
REM Offline engine tests — the regression net for the agent engine.
REM Pure / file-backed logic only: NO browser, NO LLM, NO app under test. <10s.
REM Run this before pushing changes to engine\agent_chat.py / agent_eval.py etc.
REM
REM   run_engine_tests.bat              :: selftests + the unit suite
REM   run_engine_tests.bat -k locator   :: pass extra args through to pytest
REM ─────────────────────────────────────────────────────────────────────────────
setlocal
cd /d "%~dp0"

echo [1/2] module wiring (selftests)...
venv\Scripts\python.exe engine\agent_chat.py --selftest || goto :fail
venv\Scripts\python.exe engine\agent_eval.py --selftest || goto :fail

echo [2/2] engine unit suite...
venv\Scripts\python.exe -m pytest engine\tests %*
exit /b %errorlevel%

:fail
echo SELFTEST FAILED
exit /b 1
