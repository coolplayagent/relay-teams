#!/usr/bin/env sh
set -eu

echo "Checking Python environment..."
if ! command -v python >/dev/null 2>&1; then
  echo "[Error] Python not found."
  exit 1
fi

echo "Checking uv..."
if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found, installing uv......"
  if ! pip install uv >/dev/null 2>&1; then
    echo "[Error] uv install failed."
    exit 1
  fi
fi

if [ -f "uv.lock" ]; then
  rm -f uv.lock
fi

echo "Installing dependencies (including dev tools)..."
export UV_NATIVE_TLS=1
if ! uv sync --all-extras --index-strategy unsafe-best-match; then
  echo "[Error] Dependency installation failed."
  exit 1
fi

echo "install git hooks...."
if uv run pre-commit install; then
  echo "Git Hooks install successful"
else
  echo ""
  echo "[WARNING] Git Hooks install failed"
fi

echo "Environment setup completed."
