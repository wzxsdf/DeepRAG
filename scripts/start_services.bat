@echo off
REM 启动DeepRAG服务

echo Starting DeepRAG services...

REM 启动API
start /B python -m apps.api.main

REM 等待API启动
timeout /t 3 /nobreak >nul

REM 启动Celery Worker
start /B celery -A apps.worker.ingestion worker -l info

echo Services started:
echo   - API: http://localhost:8000
echo   - Celery Worker: running
echo.
echo Press Ctrl+C to stop all services
