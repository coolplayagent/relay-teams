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
    echo uv not found, installing uv......
    setlocal enabledelayedexpansion
    pip install uv
    if %errorlevel% neq 0 (
        echo [ERROR] uv install failed
        pause
        exit /b 1
    )
)

if exist uv.lock del /f /q uv.lock >nul 2>&1

echo Installing dependencies (including dev tools)...
set UV_NATIVE_TLS=1
uv sync --all-extras --index-strategy unsafe-best-match
if %errorlevel% neq 0 (
    echo [Error] Dependency installation failed.
    exit /b 1
)

echo Installing project entry points...
uv pip install -e .
if %errorlevel% neq 0 (
    echo [Error] Editable project install failed.
    exit /b 1
)

echo install git hooks....
uv run pre-commit install
if %errorlevel% neq 0 (
    echo.
    echo [WARNING] Git Hooks install failed
) else (
    echo Git Hooks install successful
)

echo Environment setup completed.
