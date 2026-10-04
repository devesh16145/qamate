#!/usr/bin/env bash
# macOS/Linux launcher (equivalent of run_qamate.bat)
cd "$(dirname "$0")" || exit 1
echo "Starting QAmate..."
npx electron .
