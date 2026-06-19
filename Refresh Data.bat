@echo off
REM ===== Refresh MUनीम data from Google Sheets =====
REM Double-click to pull the latest numbers from all your sheets into Supabase.
REM Run this whenever you want the dashboard to show the newest sheet data.
cd /d "%~dp0"
echo Pulling latest data from Google Sheets into Supabase...
venv\Scripts\python.exe snapshot_all.py
echo.
echo Done. The dashboard will show fresh numbers within 2 minutes.
pause
