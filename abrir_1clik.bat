@echo off
cd /d "%~dp0"
title 1Clik
"C:\Users\Vaio\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" app.py
if errorlevel 1 pause
