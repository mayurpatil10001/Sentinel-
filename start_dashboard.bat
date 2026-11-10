@echo off
title Sentinel Market Surveillance Terminal
cd /d d:\Sentinel
echo =====================================================================
echo  SENTINEL - INSTITUTIONAL MARKET SURVEILLANCE & INTEGRITY TERMINAL
echo =====================================================================
echo.
echo Starting backend server on http://127.0.0.1:8000 ...
echo Dashboard URL: http://127.0.0.1:8000/dashboard/
echo.
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
pause

