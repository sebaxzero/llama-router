@echo off
rem llama-router — stdlib only, no venv needed.
cd /d "%~dp0"
py -3 main.py %*
if errorlevel 1 pause
