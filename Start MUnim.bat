@echo off
REM ===== Start MUनीम =====
REM Double-click this to launch the live dashboard on your laptop.
cd /d "%~dp0"
echo Starting MUnim dashboard...
start "" http://localhost:8787
venv\Scripts\python.exe munim_api.py
pause
