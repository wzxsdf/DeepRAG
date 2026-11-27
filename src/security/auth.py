# ponytail: JWT auth - passlib bcrypt密码 + jose签发/校验token + get_current_user依赖

import os
from datetime import datetime, timedelta, timezone

from passlib.context import CryptContext
from jose import jwt, JWTError
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
SECRET_KEY = os.getenv("JWT_SECRET", "deeprag-dev-secret-change-in-prod")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def hash_password(p: str) -> str:
    return _pwd_context.hash(p)


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed:
        return False
    return _pwd_context.verify(plain, hashed)


def create_access_token(user_id: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode({"sub": user_id, "exp": exp}, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> str | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM]).get("sub")
    except JWTError:
        return None


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """解token→查users表→返 {user_id, tenant_id, email}. 业务接口用Depends保护.
    P3: 优先复用lifespan共享池(/query强制鉴权后每请求都查users, 独立建池connect/close开销不可接受);
    无共享池场景(脚本/单测)兜底独立短连接."""
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未认证", headers={"WWW-Authenticate": "Bearer"})
    user_id = decode_token(token)
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token无效或已过期")

    sql = "SELECT id, tenant_id, email, name FROM users WHERE id = %s"
    shared = None
    try:  # lazy import(避免模块级依赖lifespan的装配顺序)
        from src.core.lifespan import get_mysql
        shared = get_mysql()
    except Exception:
        shared = None
    if shared is not None and getattr(shared, "pool", None) is not None:
        row = await shared.fetchrow(sql, user_id)
    else:
        from src.storage.mysql import MySQLStore
        mysql = MySQLStore(host=os.getenv("MYSQL_HOST", ""), port=int(os.getenv("MYSQL_PORT", "3306")),
                           db=os.getenv("MYSQL_DATABASE", os.getenv("MYSQL_DB", "deeprag")),
                           user=os.getenv("MYSQL_USER", ""), password=os.getenv("MYSQL_PASSWORD", ""))
        await mysql.connect()
        try:
            row = await mysql.fetchrow(sql, user_id)
        finally:
            await mysql.close()
    if not row:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在")
    return {"user_id": row["id"], "tenant_id": row["tenant_id"] or "default",
            "email": row["email"], "name": row.get("name", "")}
