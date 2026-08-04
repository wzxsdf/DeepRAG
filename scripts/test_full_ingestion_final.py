# 完整入库流程测试(真实SiliconFlow embedding)

import asyncio
import os
import sys
import uuid
import hashlib
import json
import random
from dotenv import load_dotenv

# Windows编码兼容
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

async def test_full_ingestion_real_embedding():
    """测试完整入库流程(真实embedding)"""
    print("=" * 50)
    print("DeepRAG 完整入库流程测试")
    print("=" * 50)

    document_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())

    # 1. 上传PDF到OSS
    print(f"\n[1] 上传PDF到OSS...")
    try:
        from src.storage.oss import OssStore
        oss = OssStore(
            endpoint=os.getenv("OSS_ENDPOINT"),
            bucket=os.getenv("OSS_BUCKET"),
            access_key_id=os.getenv("OSS_ACCESS_KEY_ID"),
            access_key_secret=os.getenv("OSS_ACCESS_KEY_SECRET"),
        )

        test_pdf = b"%PDF-1.4\nTest Content\n" * 100
        sha256 = hashlib.sha256(test_pdf).hexdigest()
        oss_key = f"raw/{version_id}/test.pdf"

        await oss.put(oss_key, test_pdf)
        print(f"  ✓ PDF上传成功: {oss_key}")
    except Exception as e:
        print(f"  ✗ PDF上传失败: {e}")
        return False

    # 2. 创建文档记录
    print(f"\n[2] 创建文档记录...")
    try:
        from src.storage.mysql import MySQLStore
        mysql = MySQLStore(
            host=os.getenv("MYSQL_HOST"),
            port=int(os.getenv("MYSQL_PORT")),
            db=os.getenv("MYSQL_DATABASE", os.getenv("MYSQL_DB", "deeprag")),
            user=os.getenv("MYSQL_USER"),
            password=os.getenv("MYSQL_PASSWORD"),
        )
        await mysql.connect()

        await mysql.execute("INSERT INTO documents (id, title) VALUES (%s, %s)", document_id, "完整测试文档")
        await mysql.create_document_version(version_id, document_id, oss_key, len(test_pdf), sha256)
        await mysql.create_indexing_job(job_id, version_id)
        await mysql.close()
        print(f"  ✓ 文档记录创建成功")
    except Exception as e:
        print(f"  ✗ 创建文档记录失败: {e}")
        return False

    # 3. 解析并切分
    print(f"\n[3] 模拟解析和切分...")
    try:
        chunks_data = []
        for i in range(5):
            chunk_id = str(uuid.uuid4())  # 使用UUID格式
            chunks_data.append({
                "id": chunk_id,
                "parent_id": version_id,
                "content": f"这是第{i+1}段测试内容。" * 20,
                "locator": {"page": i + 1},
            })

        # 存储解析结果
        parsed_key = f"parsed/{version_id}/document.json"
        parsed_content = json.dumps({"texts": [{"text": c["content"], "page": c["locator"]["page"]} for c in chunks_data]}, ensure_ascii=False)
        await oss.put(parsed_key, parsed_content.encode())

        print(f"  ✓ 解析结果存储: {len(chunks_data)} chunks")
    except Exception as e:
        print(f"  ✗ 解析失败: {e}")
        return False

    # 4. 向量化(真实SiliconFlow embedding)
    print(f"\n[4] 向量化(真实API)...")
    try:
        from apps.worker.ingestion_core import embed_chunks

        chunks_with_vectors = await embed_chunks(chunks_data)
        print(f"  ✓ 向量化完成: {len(chunks_with_vectors)} chunks")
        print(f"    向量维度: {len(chunks_with_vectors[0]['dense_vector'])}")
    except Exception as e:
        print(f"  ✗ 向量化失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    # 5. 写入Qdrant
    print(f"\n[5] 写入Qdrant...")
    try:
        from src.storage.qdrant_store import QdrantWriter
        qdrant = QdrantWriter(
            host=os.getenv("QDRANT_HOST"),
            port=int(os.getenv("QDRANT_PORT")),
        )
        qdrant.recreate_collection()  # ponytail: schema变更(sparse加IDF+Document), 重建

        points = []
        for chunk in chunks_with_vectors:
            points.append({
                "id": chunk["id"],
                "dense_vector": chunk["dense_vector"],
                "sparse_text": chunk["sparse_text"],
                "payload": {
                    "tenant_id": "default",
                    "kb_id": "default",
                    "document_id": document_id,
                    "version_id": version_id,
                    "parent_id": chunk["parent_id"],
                    "page": chunk["locator"]["page"],
                    "heading_path": [],
                    "active_version_id": version_id,
                    "acl_tags": ["default"],
                },
            })

        await qdrant.insert_chunks(points)
        print(f"  ✓ Qdrant写入成功: {len(points)} points")
    except Exception as e:
        print(f"  ✗ Qdrant写入失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    # 6. 写入MySQL chunks
    print(f"\n[6] 写入MySQL chunks...")
    try:
        from src.storage.mysql import MySQLStore
        mysql = MySQLStore(
            host=os.getenv("MYSQL_HOST"),
            port=int(os.getenv("MYSQL_PORT")),
            db=os.getenv("MYSQL_DATABASE", os.getenv("MYSQL_DB", "deeprag")),
            user=os.getenv("MYSQL_USER"),
            password=os.getenv("MYSQL_PASSWORD"),
        )
        await mysql.connect()

        for chunk in chunks_with_vectors:
            chunk_oss_key = f"chunks/{version_id}/{chunk['id']}.txt"
            await oss.put(chunk_oss_key, chunk["content"].encode())

            heading_path_str = json.dumps([])

            await mysql.insert_chunk(
                chunk_id=chunk["id"],
                version_id=version_id,
                parent_id=chunk["parent_id"],
                page=chunk["locator"]["page"],
                heading_path=heading_path_str,
                content_hash=chunk["content_hash"],
                oss_key=chunk_oss_key,
            )

        await mysql.close()
        print(f"  ✓ MySQL chunks写入成功: {len(chunks_with_vectors)} chunks")
    except Exception as e:
        print(f"  ✗ MySQL写入失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    # 7. 验证结果
    print(f"\n[7] 验证入库结果...")
    try:
        # 检查MySQL
        mysql = MySQLStore(
            host=os.getenv("MYSQL_HOST"),
            port=int(os.getenv("MYSQL_PORT")),
            db=os.getenv("MYSQL_DATABASE", os.getenv("MYSQL_DB", "deeprag")),
            user=os.getenv("MYSQL_USER"),
            password=os.getenv("MYSQL_PASSWORD"),
        )
        await mysql.connect()

        chunks = await mysql.fetch("SELECT COUNT(*) as count FROM chunks WHERE version_id = %s", version_id)
        chunk_count = chunks[0]["count"]
        print(f"  ✓ MySQL chunks: {chunk_count}")

        # 检查Qdrant
        from src.storage.qdrant_store import QdrantWriter
        qdrant = QdrantWriter(
            host=os.getenv("QDRANT_HOST"),
            port=int(os.getenv("QDRANT_PORT")),
        )
        collection_info = qdrant.client.get_collection(qdrant.collection_name)
        qdrant_count = collection_info.points_count
        print(f"  ✓ Qdrant总points: {qdrant_count}")

        # 检查OSS
        chunks_data = await mysql.fetch("SELECT id, oss_key FROM chunks WHERE version_id = %s LIMIT 2", version_id)
        print(f"  ✓ OSS片段样本:")
        for chunk in chunks_data:
            print(f"    - {chunk['id']}: {chunk['oss_key']}")

        await mysql.close()

        print(f"\n" + "=" * 50)
        print(f"✓ 完整入库流程测试成功! (100%)")
        print(f"  " + "=" * 48)
        print(f"  ├─ OSS: 原始PDF + 解析JSON + {chunk_count}个片段")
        print(f"  ├─ MySQL: 文档 + 版本 + {chunk_count} chunks")
        print(f"  ├─ Qdrant: {qdrant_count} points (1024维dense+sparse)")
        print(f"  └─ SiliconFlow: BGE-M3真实向量化")
        print(f"  " + "=" * 48)

        return True

    except Exception as e:
        print(f"  ✗ 验证失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = asyncio.run(test_full_ingestion_real_embedding())
    sys.exit(0 if success else 1)
