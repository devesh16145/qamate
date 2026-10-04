#!/usr/bin/env bash
# Offline engine tests (macOS/Linux equivalent of run_engine_tests.bat). Extra args go to pytest.
set -e
cd "$(dirname "$0")"
PY=venv/bin/python
echo "[1/2] module wiring (selftests)..."
$PY engine/agent_chat.py --selftest
$PY engine/agent_eval.py --selftest
echo "[2/2] engine unit suite..."
$PY -m pytest engine/tests "$@"
