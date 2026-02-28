@echo off
chcp 65001 >nul
echo 正在检查 Python 环境...

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python 并将其添加到系统环境变量 PATH 中！
    pause
    exit /b 1
)

echo Python 已安装，正在安装 uv...
python -m pip install uv

if %errorlevel% neq 0 (
    echo [错误] uv 安装失败！
    pause
    exit /b 1
)

echo uv 安装成功，正在执行 uv sync...
uv sync

if %errorlevel% neq 0 (
    echo [错误] uv sync 执行失败！
    pause
    exit /b 1
)

echo [成功] 环境配置完成！
pause
