@echo off
REM ===== Refresh MUनीम and publish to Cohesivity =====
REM Double-click to: pull latest sheet data, regenerate AI insights, and
REM republish the live dashboard at pure-shrimp-nesting.cohesivity.app
cd /d "%~dp0"
echo [1/3] Pulling latest data from Google Sheets...
venv\Scripts\python.exe snapshot_all.py
echo.
echo [2/3] Building data + AI insights into Cohesivity...
venv\Scripts\python.exe cohesivity_sync.py
echo.
echo [3/3] Publishing the live site...
venv\Scripts\python.exe cohesivity_deploy.py
echo.
echo Done. Live at https://pure-shrimp-nesting.cohesivity.app
pause
