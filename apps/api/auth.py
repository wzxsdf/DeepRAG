# ponytail: 注册/登录路由 — 经UserRepository; DDL(name/password_hash列)收敛到lifespan ensure_schema
import uuid
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

from src.security.auth import hash_password, verify_password, create_access_token, get_current_user
from src.storage.mysql import MySQLStore
from src.storage.repositories import UserRepository
from src.core.lifespan import get_mysql

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


class AuthResponse(BaseModel):
    token: str
    user: dict


@router.post("/register", response_model=AuthResponse)
async def register(req: RegisterRequest, mysql: MySQLStore = Depends(get_mysql)):
    if len(req.password) < 6:
        raise HTTPException(400, "密码至少6位")
    repo = UserRepository(mysql)
    if await repo.find_by_email(req.email):
        raise HTTPException(400, "邮箱已注册")
    user_id = str(uuid.uuid4())
    await repo.create(user_id, "default", req.email, req.name, hash_password(req.password))
    return AuthResponse(token=create_access_token(user_id),
                        user={"id": user_id, "email": req.email, "name": req.name, "tenant_id": "default"})


@router.post("/login", response_model=AuthResponse)
async def login(req: LoginRequest, mysql: MySQLStore = Depends(get_mysql)):
    repo = UserRepository(mysql)
    u = await repo.find_by_email(req.email)
    if not u or not verify_password(req.password, u.password_hash or ""):
        raise HTTPException(401, "邮箱或密码错误")
    return AuthResponse(token=create_access_token(u.id),
                        user={"id": u.id, "email": u.email, "name": u.name or "", "tenant_id": u.tenant_id or "default"})


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    """前端校验token: 返回当前用户信息."""
    return user
