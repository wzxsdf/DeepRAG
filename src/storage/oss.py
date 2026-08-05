# ponytail: 只实现W1需要的方法,presign_upload+put+get

import oss2
from .protocol import ObjectStore, ObjectMeta, PresignedUpload
from src.core.config import OSSCfg

class OssStore(ObjectStore):
    def __init__(
        self,
        cfg: OSSCfg | None = None,
        endpoint: str = "",
        bucket: str = "deeprag-dev",
        access_key_id: str = "",
        access_key_secret: str = "",
    ):
        if cfg is not None:  # ponytail: cfg注入(阶段1), 旧kwargs向后兼容
            endpoint, bucket = cfg.endpoint, cfg.bucket
            access_key_id, access_key_secret = cfg.access_key_id, cfg.access_key_secret
        auth = oss2.Auth(access_key_id, access_key_secret)
        self.bucket = oss2.Bucket(auth, endpoint, bucket)

    async def presign_upload(self, key: str, expires: int = 3600) -> PresignedUpload:
        # ponytail: OSS sign_url参数: method, key, expires, headers=None
        url = self.bucket.sign_url("PUT", key, expires)
        return PresignedUpload(url=url, key=key, headers={})

    async def put(self, key: str, data: bytes) -> ObjectMeta:
        self.bucket.put_object(key, data)
        meta = self.bucket.head_object(key)
        return ObjectMeta(key=key, size=meta.content_length, etag=meta.etag)

    async def get(self, key: str) -> bytes:
        return self.bucket.get_object(key).read()

    async def presign_get(self, key: str, expires: int = 3600) -> str:
        # ponytail: 预签名GET URL, 供前端文档预览(原PDF在线查看)
        return self.bucket.sign_url("GET", key, expires)

    async def delete(self, key: str):
        # ponytail: 删单个对象, 幂等(不存在静默)
        try:
            self.bucket.delete_object(key)
        except oss2.exceptions.NoSuchKey:
            pass

    async def delete_by_prefix(self, prefix: str):
        # ponytail: 按前缀批量删(list+batch_delete, 1000/批), 清 parsed/{vid}/, chunks/{vid}/ 等
        keys = [obj.key for obj in oss2.ObjectIterator(self.bucket, prefix=prefix)]
        for i in range(0, len(keys), 1000):
            self.bucket.batch_delete_objects(keys[i:i + 1000])
