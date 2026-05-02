"""
Auth JWT statique — bootstrap rapide, sans Keycloak
Users définis dans users.json ou ENV GATEWAY_USERS
"""
import json, os, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from pydantic import BaseModel

JWT_SECRET     = os.getenv("JWT_SECRET", "CHANGE-ME-IN-PROD-32chars-minimum!")
JWT_ALGO       = "HS256"
JWT_EXPIRE_H   = int(os.getenv("JWT_EXPIRE_HOURS", "24"))
USERS_FILE     = Path(os.getenv("USERS_FILE", "./users.json"))

DEFAULT_USERS = [
    {"username": "admin",  "password": "admin123",  "roles": ["admin", "ops", "sre", "dev"]},
    {"username": "sylvain","password": "sylvain123","roles": ["admin", "ops", "sre"]},
    {"username": "ops",    "password": "ops123",    "roles": ["ops", "sre"]},
    {"username": "dev",    "password": "dev123",    "roles": ["dev"]},
]

def _load_users() -> list[dict]:
    if USERS_FILE.exists():
        return json.loads(USERS_FILE.read_text())
    raw = os.getenv("GATEWAY_USERS")
    if raw:
        return json.loads(raw)
    return DEFAULT_USERS

def create_token(username: str, roles: list[str]) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({
        "sub": username, "roles": roles,
        "iat": now, "exp": now + timedelta(hours=JWT_EXPIRE_H),
        "jti": str(uuid.uuid4()),
    }, JWT_SECRET, algorithm=JWT_ALGO)

def _verify(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except JWTError as e:
        raise HTTPException(401, f"Token invalide: {e}")

_bearer = HTTPBearer(auto_error=False)

async def current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> dict:
    if not creds:
        raise HTTPException(401, "Authorization: Bearer <token> manquant")
    p = _verify(creds.credentials)
    return {"sub": p["sub"], "roles": p.get("roles", []), "jti": p.get("jti")}

def require_admin(user: dict = Depends(current_user)) -> dict:
    if "admin" not in user["roles"]:
        raise HTTPException(403, "Rôle admin requis")
    return user

# ── Router ────────────────────────────────────────────────────────────────────
router = APIRouter(prefix="/auth", tags=["auth"])

class LoginReq(BaseModel):
    username: str
    password: str

@router.post("/token")
async def login(body: LoginReq):
    user = next((u for u in _load_users()
                 if u["username"] == body.username and u["password"] == body.password), None)
    if not user:
        raise HTTPException(401, "Identifiants incorrects")
    token = create_token(user["username"], user["roles"])
    return {
        "access_token": token, "token_type": "bearer",
        "username": user["username"], "roles": user["roles"],
        "expires_in": JWT_EXPIRE_H * 3600,
    }

@router.get("/me")
async def me(user: dict = Depends(current_user)):
    return user

@router.get("/users")
async def list_users(user: dict = Depends(require_admin)):
    return [{"username": u["username"], "roles": u["roles"]} for u in _load_users()]
