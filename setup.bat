@echo off
chcp 65001 >nul

echo Checking Python environment...
set "PYTHON_CMD="
py -3 --version >nul 2>&1
if %errorlevel% equ 0 (
    set "PYTHON_CMD=py -3"
)
if "%PYTHON_CMD%"=="" python --version >nul 2>&1
if %errorlevel% equ 0 if "%PYTHON_CMD%"=="" set "PYTHON_CMD=python"

if "%PYTHON_CMD%"=="" (
    echo [Error] Python not found.
    exit /b 1
)

echo Checking uv...
set "UV_CMD="
%PYTHON_CMD% -m uv --version >nul 2>&1
if %errorlevel% equ 0 set "UV_CMD=%PYTHON_CMD% -m uv"
if "%UV_CMD%"=="" uv --version >nul 2>&1
if %errorlevel% equ 0 if "%UV_CMD%"=="" set "UV_CMD=uv"
if "%UV_CMD%"=="" (
    echo uv not found, installing uv......
    %PYTHON_CMD% -m pip install uv
    if %errorlevel% neq 0 (
        echo [ERROR] uv install failed
        pause
        exit /b 1
    )
    set "UV_CMD=%PYTHON_CMD% -m uv"
)

if exist uv.lock del /f /q uv.lock >nul 2>&1

echo Installing dependencies (including dev tools)...
set UV_NATIVE_TLS=1
%UV_CMD% sync --all-extras --index-strategy unsafe-best-match
if %errorlevel% neq 0 (
    echo [Error] Dependency installation failed.
    exit /b 1
)

echo Installing project entry points...
%UV_CMD% pip install -e .
if %errorlevel% neq 0 (
    echo [Error] Editable project install failed.
    exit /b 1
)

echo install git hooks....
%UV_CMD% run pre-commit install
if %errorlevel% neq 0 (
    echo.
    echo [WARNING] Git Hooks install failed
) else (
    echo Git Hooks install successful
)

echo Environment setup completed.
