@echo off
chcp 65001 >nul
cd /d %~dp0
echo Starting Tianjin AI Travel Planner...
start "" http://127.0.0.1:8000
.venv\Scripts\python.exe -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
pause
