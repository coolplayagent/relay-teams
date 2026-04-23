#!/usr/bin/env sh
set -eu

echo "Checking Python environment..."
PYTHON_BIN=""
if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

if [ -z "$PYTHON_BIN" ]; then
  echo "[Error] Python not found."
  exit 1
fi

echo "Checking uv..."
UV_MODE=""
if "$PYTHON_BIN" -m uv --version >/dev/null 2>&1; then
  UV_MODE="python-module"
elif command -v uv >/dev/null 2>&1; then
  UV_MODE="executable"
else
  echo "uv not found, installing uv......"
  if ! "$PYTHON_BIN" -m pip install uv >/dev/null 2>&1; then
    echo "[Error] uv install failed."
    exit 1
  fi
fi

if [ -z "$UV_MODE" ]; then
  UV_MODE="python-module"
fi

run_uv() {
  if [ "$UV_MODE" = "python-module" ]; then
    "$PYTHON_BIN" -m uv "$@"
    return
  fi
  uv "$@"
}

if [ -f "uv.lock" ]; then
  rm -f uv.lock
fi

echo "Installing dependencies (including dev tools)..."
export UV_NATIVE_TLS=1
if ! run_uv sync --all-extras --index-strategy unsafe-best-match; then
  echo "[Error] Dependency installation failed."
  exit 1
fi

echo "Installing project entry points..."
if ! run_uv pip install -e .; then
  echo "[Error] Editable project install failed."
  exit 1
fi

echo "install git hooks...."
if run_uv run pre-commit install; then
  echo "Git Hooks install successful"
else
  echo ""
  echo "[WARNING] Git Hooks install failed"
fi

echo "Environment setup completed."
