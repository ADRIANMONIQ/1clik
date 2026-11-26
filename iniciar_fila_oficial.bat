@echo off
setlocal
cd /d "%~dp0"
"C:\Users\Vaio\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" campaign_worker.py
endlocal
