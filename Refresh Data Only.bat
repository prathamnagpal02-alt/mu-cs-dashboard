@echo off
REM ===== Sheets-only auto refresh (NO Apify, NO AI regen) =====
REM Runs on a schedule to keep all SHEET data live. Instagram numbers stay
REM from the last manual "Refresh and Publish"; AI insights are preserved.
cd /d "%~dp0"
echo [%date% %time%] Auto refresh starting...>> refresh_auto.log
venv\Scripts\python.exe snapshot_all.py >> refresh_auto.log 2>&1
venv\Scripts\python.exe cohesivity_sync.py --no-ai >> refresh_auto.log 2>&1
venv\Scripts\python.exe cohesivity_deploy.py >> refresh_auto.log 2>&1
echo [%date% %time%] Auto refresh done.>> refresh_auto.log
