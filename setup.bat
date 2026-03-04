@echo off
chcp 65001 >nul

echo Checking Python environment...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [Error] Python not found.
    exit /b 1
)

echo Checking uv...
uv --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [Error] uv not found. Please install uv first: https://github.com/astral-sh/uv
    exit /b 1
)

echo Installing dependencies (including dev tools)...
uv sync --extra dev
if %errorlevel% neq 0 (
    echo [Error] Dependency installation failed.
    exit /b 1
)

echo Environment setup completed.
