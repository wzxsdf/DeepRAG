# ponytail: query的dense embedding, 复用SiliconFlow BGE-M3 (单条+重试)

import os
import asyncio
import httpx
from dotenv import load_dotenv

load_dotenv()

_EMBED_URL = os.getenv("EMBEDDING_BASE_URL", "") + "/embeddings"
_EMBED_HEADERS = {"Authorization": f"Bearer {os.getenv('EMBEDDING_API_KEY', '')}"}
_EMBED_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")


async def embed_query(text: str) -> list[float]:
    """单条query的dense embedding. API偶发ReadError故带重试."""
    payload = {"model": _EMBED_MODEL, "input": [text], "encoding_format": "float"}
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post(_EMBED_URL, headers=_EMBED_HEADERS, json=payload)
            r.raise_for_status()
            return r.json()["data"][0]["embedding"]
        except Exception:
            if attempt == 2:
                raise
            await asyncio.sleep(1.5 * (attempt + 1))
