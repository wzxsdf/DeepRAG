# W1 验证指南

## 方案A:本地有Docker(推荐)

```powershell
# 1. 启动依赖服务
docker compose up -d

# 2. 初始化MySQL表结构
mysql -h localhost -U deeprag -p deeprag < src/storage/mysql_schema.sql
# 或用Docker exec
docker exec -i deeprag-mysql-1 mysql -u deeprag -pdeeprag deeprag < src/storage/mysql_schema.sql

# 3. 安装依赖
pip install -e .

# 4. 配置.env
cp .env.example .env
# 默认配置就能连上docker服务

# 5. 运行验证
python scripts/verify_w1.py

# 6. 手动测试API
python -m apps.api.main
# 另一个终端
curl http://localhost:8000/
```

## 方案B:无Docker(Windows本地)

跳过依赖服务,只验证模块能加载:

```powershell
# 1. 安装依赖
pip install -e .

# 2. 跳过Docker测试运行验证
set SKIP_DOCKER_TESTS=1
python scripts/verify_w1.py
```

## 方案C:用云服务替代

如果不想起Docker,可以用云服务:

```powershell
# .env配置
MYSQL_HOST=your-mysql-host.com
QDRANT_HOST=https://your-qdrant.cloud
```

然后正常运行验证脚本。

## 常见问题

### Docker不可用
Windows上Docker可能需要:
```powershell
# 用Docker Desktop的docker.exe
& "C:\Program Files\Docker\Docker\resources\bin\docker.exe" compose up -d

# 或用docker-compose.exe
docker-compose.exe up -d
```

### PSQL不可用
用Docker exec执行:
```powershell
docker exec -it deeprag-postgres-1 psql -U deeprag -d deeprag
```

### BGE-M3下载慢
首次运行会下载模型(~2GB),自动缓存到本地。

## 预期输出

```
==================================================
DeepRAG W1 验证
==================================================
测试导入...
✓ storage模块导入成功
✓ apps模块导入成功

测试PG连接...
✓ PG连接成功: PostgreSQL 16...

测试Qdrant连接...
✓ Qdrant连接成功,集合已创建

测试API启动...
✓ API模块加载成功
  启动命令: python -m apps.api.main

测试Celery启动...
✓ Celery模块加载成功
  启动命令: celery -A apps.worker.ingestion worker -l info

==================================================
验证结果:
==================================================
✓ 模块导入
✓ PG连接
✓ Qdrant连接
✓ API启动
✓ Celery启动
==================================================
✓ 所有验证通过,W1就绪
```
