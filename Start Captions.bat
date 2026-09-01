@echo off
setlocal
cd /d "%~dp0"
title Campus Captions

REM Find a Python. Prefer the py launcher, fall back to python on PATH.
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"

if not defined PY (
    echo.
    echo   Python is not installed on this machine.
    echo.
    echo   Install Python 3.11 or newer from python.org and tick
    echo   "Add Python to PATH" during setup, then run this file again.
    echo.
    pause
    exit /b 1
)

%PY% launcher\launch.py

echo.
pause
