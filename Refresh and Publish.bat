@echo off
REM ===== Refresh MUनीम and publish to Cohesivity =====
REM Double-click to: pull latest sheet data, refresh Instagram stats,
REM regenerate AI insights, and republish the live dashboard.
cd /d "%~dp0"
echo [1/4] Pulling latest data from Google Sheets...
venv\Scripts\python.exe snapshot_all.py
echo.
echo [2/4] Refreshing Instagram stats (Builders.mu via Apify)...
venv\Scripts\python.exe instagram_stats.py builders.mu
echo.
echo [3/4] Building data + AI insights into Cohesivity...
venv\Scripts\python.exe cohesivity_sync.py
echo.
echo [4/4] Publishing the live site...
venv\Scripts\python.exe cohesivity_deploy.py
echo.
echo Done. Live at https://munimji.cohesivity.app
pause
