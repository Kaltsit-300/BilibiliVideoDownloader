@echo off
setlocal
title Bilibili Video Downloader

set "PYTHON=C:\Python 3.14\python.exe"
set "APP_DIR=D:\Myproject\BilibiliVideoDownloader"
set "SCRIPT=%APP_DIR%\Bilibili Video Downloader.py"
set "REQ=%APP_DIR%\requirements.txt"

if not exist "%PYTHON%" (
    echo [ERROR] Python not found: %PYTHON%
    echo Install Python 3.8+ first, then run: pip install -r "%REQ%"
    pause
    exit /b 1
)

if not exist "%SCRIPT%" (
    echo [ERROR] Main script not found: %SCRIPT%
    pause
    exit /b 1
)

echo Checking dependencies...
"%PYTHON%" -c "import requests, customtkinter, PIL, moviepy" >nul 2>&1
if errorlevel 1 (
    echo Missing dependencies, installing from Tsinghua mirror...
    "%PYTHON%" -m pip install -r "%REQ%" -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo [ERROR] Dependency install failed. Run manually:
        echo   "%PYTHON%" -m pip install -r "%REQ%"
        pause
        exit /b 1
    )
    echo Dependencies installed.
)

echo Starting Bilibili Video Downloader...
cd /d "%APP_DIR%"
"%PYTHON%" "%SCRIPT%"
if errorlevel 1 (
    echo.
    echo [ERROR] Program exited abnormally. See messages above.
    pause
)
endlocal
