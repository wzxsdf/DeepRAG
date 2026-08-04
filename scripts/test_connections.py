# ponytail: 测试真实连接的脚本

import os
import sys
import asyncio
from dotenv import load_dotenv

# 加载.env
load_dotenv()

# Windows编码兼容
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def test_mysql():
    """测试MySQL连接"""
    print("\n测试MySQL连接...")
    try:
        from src.storage.mysql import MySQLStore

        mysql = MySQLStore(
            host=os.getenv("MYSQL_HOST", "localhost"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            db=os.getenv("MYSQL_DATABASE", os.getenv("MYSQL_DB", "deeprag")),
            user=os.getenv("MYSQL_USER", "deeprag"),
            password=os.getenv("MYSQL_PASSWORD", "deeprag_dev"),
        )

        async def test():
            await mysql.connect()
            result = await mysql.fetchrow("SELECT VERSION() as version")
            await mysql.close()
            return result

        version = asyncio.run(test())
        print(f"  ✓ MySQL连接成功: {list(version.values())[0]}")
        return True
    except Exception as e:
        print(f"  ✗ MySQL连接失败: {e}")
        return False

def test_oss():
    """测试OSS连接"""
    print("\n测试OSS连接...")
    try:
        from src.storage.oss import OssStore

        oss = OssStore(
            endpoint=os.getenv("OSS_ENDPOINT", ""),
            bucket=os.getenv("OSS_BUCKET", ""),
            access_key_id=os.getenv("OSS_ACCESS_KEY_ID", ""),
            access_key_secret=os.getenv("OSS_ACCESS_KEY_SECRET", ""),
        )

        async def test():
            # 测试list bucket
            presign = await oss.presign_upload("test.txt", expires=3600)
            return presign

        presign = asyncio.run(test())
        print(f"  ✓ OSS连接成功: {os.getenv('OSS_BUCKET')}")
        print(f"    预签名URL示例: {presign.url[:50]}...")
        return True
    except Exception as e:
        print(f"  ✗ OSS连接失败: {e}")
        return False

def test_qdrant():
    """测试Qdrant连接"""
    print("\n测试Qdrant连接...")
    try:
        from src.storage.qdrant_store import QdrantWriter

        qdrant = QdrantWriter(
            host=os.getenv("QDRANT_HOST", "http://localhost"),
            port=int(os.getenv("QDRANT_PORT", "6333")),
        )

        qdrant.ensure_collection()
        print(f"  ✓ Qdrant连接成功,集合已创建: {qdrant.collection_name}")
        return True
    except Exception as e:
        print(f"  ✗ Qdrant连接失败: {e}")
        return False

def main():
    print("=" * 50)
    print("DeepRAG 连接测试")
    print("=" * 50)

    results = []

    results.append(("MySQL", test_mysql()))
    results.append(("OSS", test_oss()))
    results.append(("Qdrant", test_qdrant()))

    print("\n" + "=" * 50)
    print("测试结果:")
    print("=" * 50)
    for name, passed in results:
        status = "✓" if passed else "✗"
        print(f"{status} {name}")

    all_passed = all(r[1] for r in results)
    print("=" * 50)
    if all_passed:
        print("✓ 所有连接测试通过")
    else:
        print("✗ 部分连接失败,请检查配置")

    return 0 if all_passed else 1

if __name__ == "__main__":
    sys.exit(main())
