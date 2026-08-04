# 初始化MySQL表结构

import asyncio
import os
import sys
from dotenv import load_dotenv

# Windows编码兼容
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

async def init_tables():
    from src.storage.mysql import MySQLStore

    mysql = MySQLStore(
        host=os.getenv("MYSQL_HOST"),
        port=int(os.getenv("MYSQL_PORT")),
        db=os.getenv("MYSQL_DATABASE", os.getenv("MYSQL_DB", "deeprag")),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
    )

    await mysql.connect()

    # 读取schema文件
    with open("src/storage/mysql_schema.sql", "r", encoding="utf-8") as f:
        schema = f.read()

    # 分割SQL语句并执行
    statements = [s.strip() for s in schema.split(";") if s.strip() and not s.strip().startswith("--")]

    for stmt in statements:
        try:
            await mysql.execute(stmt)
            print(f"✓ {stmt[:60]}...")
        except Exception as e:
            if "already exists" not in str(e):
                print(f"✗ {stmt[:60]}... → {e}")

    # ponytail: 已存在的表补生命周期列(CREATE TABLE IF NOT EXISTS不改已有表, 须ALTER)
    alter_specs = [
        ("knowledge_bases", "status", "ADD COLUMN status VARCHAR(20) DEFAULT 'ACTIVE'"),
        ("knowledge_bases", "deleted_at", "ADD COLUMN deleted_at TIMESTAMP NULL"),
        ("knowledge_bases", "deleted_by", "ADD COLUMN deleted_by VARCHAR(50)"),
        ("documents", "deleted_at", "ADD COLUMN deleted_at TIMESTAMP NULL"),
        ("documents", "deleted_by", "ADD COLUMN deleted_by VARCHAR(50)"),
    ]
    for table, col, add_sql in alter_specs:
        exists = await mysql.fetch(f"SHOW COLUMNS FROM `{table}` LIKE %s", col)
        if not exists:
            try:
                await mysql.execute(f"ALTER TABLE `{table}` {add_sql}")
                print(f"✓ ALTER {table}.{col}")
            except Exception as e:
                print(f"✗ ALTER {table}.{col} → {e}")

    await mysql.close()
    print("\n✓ MySQL表初始化完成")

if __name__ == "__main__":
    asyncio.run(init_tables())
