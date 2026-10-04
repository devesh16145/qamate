#!/usr/bin/env bash
# Stage a relocatable Python (python-build-standalone) at build/pybundle/python for macOS packaging.
# Optional: without it, first run falls back to a system Python 3.11+. Mirrors prepare-bundle.ps1.
# Usage: bash scripts/prepare-bundle.sh [pythonVersion] [releaseTag]
set -euo pipefail
PYV="${1:-3.12.7}"; TAG="${2:-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/build/pybundle"
[ -x "$DEST/python/bin/python3" ] && { echo "Already staged at $DEST/python"; exit 0; }
case "$(uname -m)" in arm64) ARCH=aarch64;; *) ARCH=x86_64;; esac
if [ -z "$TAG" ]; then
  TAG=$(curl -fsSL https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest | sed -n 's/.*"tag_name": *"\([^"]*\)".*/\1/p' | head -1 || true)
  TAG="${TAG:-20241016}"
fi
ASSET="cpython-${PYV}+${TAG}-${ARCH}-apple-darwin-install_only.tar.gz"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
echo "Downloading $ASSET"
curl -fSL "https://github.com/astral-sh/python-build-standalone/releases/download/${TAG}/${ASSET}" -o "$TMP/py.tgz"
mkdir -p "$DEST"; tar -xzf "$TMP/py.tgz" -C "$DEST"
"$DEST/python/bin/python3" --version
