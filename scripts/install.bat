@echo off
REM DeepRAG W1 依赖安装脚本

echo ========================================
echo DeepRAG W1 依赖安装
echo ========================================

echo.
echo [1/3] 升级pip...
python -m pip install --upgrade pip

echo.
echo [2/3] 安装项目依赖...
pip install -e .

echo.
echo [3/3] 验证安装...
set SKIP_DOCKER_TESTS=1
python scripts\verify_w1.py

echo.
echo ========================================
echo 安装完成!
echo ========================================
echo.
echo 下一步:
echo   1. 配置环境变量: copy .env.example .env
echo   2. 启动API: python -m apps.api.main
echo   3. 启动Worker: celery -A apps.worker.ingestion worker -l info
echo.
