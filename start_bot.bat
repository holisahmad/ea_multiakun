@echo off
title STMA Central Controller
cd /d "D:\project-code\ea-project\ea_multiakun"
echo ============================================================
echo   Smart Trade Multi Account (STMA) Central Controller
echo ============================================================
echo   Server URL : http://127.0.0.1:8000
echo   Dashboard  : http://127.0.0.1:8000/dashboard-ui
echo ============================================================
echo.
python -m uvicorn controller.main:app --host 0.0.0.0 --port 8000
pause
