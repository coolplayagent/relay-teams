#!/usr/bin/env sh
set -eu

echo "Checking Python environment..."
if ! command -v python >/dev/null 2>&1; then
  echo "[Error] Python not found."
  exit 1
fi

echo "Checking uv..."
if ! command -v uv >/dev/null 2>&1; then
  echo "[Error] uv not found. Install uv first: https://github.com/astral-sh/uv"
  exit 1
fi

echo "Installing dependencies (including dev tools)..."
uv sync --extra dev

echo "Environment setup completed."
