"""
Auth — JWT statique + API tokens (format: mcp_<64 hex chars>)
Users définis dans users.json ou ENV GATEWAY_USERS
Mots de passe : bcrypt via champ "password_hash" (le champ "password" en clair
reste accepté pour le dev, avec warning).
"""
import hashlib, json, logging, os, secrets, time, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import Boolean, Column, DateTime, select, String
from sqlalchemy.ext.asyncio import AsyncSession

from .db import Base, get_db

log = logging.getLogger("auth")

# ── JWT secret : fail-fast si absent ou non sécurisé ──────────────────────────
_KNOWN_BAD_SECRETS = {
    "CHANGE-ME-IN-PROD-32chars-minimum!",
    "CHANGE-ME-minimum-32-characters-secret",
}
JWT_SECRET = os.getenv("JWT_SECRET", "")
if not JWT_SECRET or JWT_SECRET in _KNOWN_BAD_SECRETS or len(JWT_SECRET) < 32:
    raise RuntimeError(
        "JWT_SECRET manquant, trop court (<32 caractères) ou égal à une valeur d'exemple publiée. "
        "Générer un secret aléatoire : openssl rand -hex 32"
    )

JWT_ALGO       = "HS256"
JWT_EXPIRE_H   = int(os.getenv("JWT_EXPIRE_HOURS", "24"))
USERS_FILE     = Path(os.getenv("USERS_FILE", "./users.json"))

# Comptes de démo — uniquement si ALLOW_DEFAULT_USERS=true (jamais en prod)
ALLOW_DEFAULT_USERS = os.getenv("ALLOW_DEFAULT_USERS", "false").lower() == "true"
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
    if ALLOW_DEFAULT_USERS:
        log.warning("ALLOW_DEFAULT_USERS=true — comptes de démo actifs (admin/admin123…). Ne JAMAIS utiliser en production.")
        return DEFAULT_USERS
    log.error("Aucun utilisateur configuré — créer users.json ou définir GATEWAY_USERS "
              "(hash bcrypt via: python -m gateway.hash_password)")
    return []

def _check_password(user: dict, password: str) -> bool:
    """password_hash (bcrypt) prioritaire ; 'password' en clair toléré en dev avec warning."""
    h = user.get("password_hash")
    if h:
        try:
            return bcrypt.checkpw(password.encode(), h.encode())
        except ValueError:
            log.error("password_hash invalide pour %s (attendu: hash bcrypt)", user.get("username"))
            return False
    pw = user.get("password")
    if pw is not None:
        log.warning("Utilisateur '%s' avec mot de passe en clair — migrer vers password_hash "
                    "(python -m gateway.hash_password)", user.get("username"))
        return secrets.compare_digest(pw, password)
    return False

def create_token(username: str, roles: list[str]) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({
        "sub": username, "roles": roles,
        "iat": now, "exp": now + timedelta(hours=JWT_EXPIRE_H),
        "jti": str(uuid.uuid4()),
    }, JWT_SECRET, algorithm=JWT_ALGO)

# ── JWT in-process TTL cache ──────────────────────────────────────────────────
# Stores decoded payloads keyed by token hash to avoid repeated crypto decode.
# Per-worker only (not distributed) — intentional: keeps sensitive data local.
_jwt_cache: dict[str, tuple[dict, float]] = {}  # hash → (payload, exp_ts)
_JWT_CACHE_MAX = 2000

def _verify_jwt(token: str) -> dict:
    key = hashlib.sha256(token.encode()).hexdigest()
    cached = _jwt_cache.get(key)
    if cached:
        payload, exp = cached
        if time.time() < exp:
            return payload
        del _jwt_cache[key]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except JWTError as e:
        raise HTTPException(401, f"Token invalide: {e}")
    # Evict oldest 10% when cache is full
    if len(_jwt_cache) >= _JWT_CACHE_MAX:
        oldest = sorted(_jwt_cache, key=lambda k: _jwt_cache[k][1])[:_JWT_CACHE_MAX // 10]
        for k in oldest:
            del _jwt_cache[k]
    _jwt_cache[key] = (payload, float(payload.get("exp", time.time() + 3600)))
    return payload

# ── ApiToken ORM model ────────────────────────────────────────────────────────
class ApiToken(Base):
    __tablename__ = "api_tokens"
    id           = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name         = Column(String, nullable=False)
    token_hash   = Column(String, nullable=False, unique=True)
    username     = Column(String, nullable=False)
    roles        = Column(String, nullable=False)  # JSON-encoded list
    created_at   = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    expires_at   = Column(DateTime, nullable=True)
    last_used_at = Column(DateTime, nullable=True)
    enabled      = Column(Boolean, default=True)

def _token_dict(t: ApiToken) -> dict:
    return {
        "id": t.id, "name": t.name, "username": t.username,
        "roles": json.loads(t.roles),
        "created_at":   t.created_at.isoformat()   if t.created_at   else None,
        "expires_at":   t.expires_at.isoformat()   if t.expires_at   else None,
        "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
        "enabled": t.enabled,
    }

async def _verify_api_token(raw: str, db: AsyncSession) -> dict:
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    row = (await db.execute(
        select(ApiToken).where(ApiToken.token_hash == token_hash, ApiToken.enabled == True)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(401, "Token API invalide ou révoqué")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if row.expires_at and row.expires_at < now:
        raise HTTPException(401, "Token API expiré")
    row.last_used_at = now
    await db.commit()
    return {"sub": row.username, "roles": json.loads(row.roles), "token_id": row.id}

# ── FastAPI auth dependency ───────────────────────────────────────────────────
_bearer = HTTPBearer(auto_error=False)

async def current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not creds:
        raise HTTPException(401, "Authorization: Bearer <token> manquant")
    token = creds.credentials
    if token.startswith("mcp_"):
        return await _verify_api_token(token, db)
    p = _verify_jwt(token)
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
    user = next((u for u in _load_users() if u["username"] == body.username), None)
    if not user or not _check_password(user, body.password):
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

# ── API Token CRUD ────────────────────────────────────────────────────────────
class TokenCreate(BaseModel):
    name:         str
    username:     Optional[str]       = None
    roles:        Optional[list[str]] = None
    expires_days: Optional[int]       = None  # None = never expires

@router.post("/tokens", status_code=201)
async def create_api_token(
    body: TokenCreate,
    user: dict = Depends(require_admin),
    db:   AsyncSession = Depends(get_db),
):
    raw        = "mcp_" + secrets.token_hex(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    username   = body.username or user["sub"]
    roles      = body.roles    or user["roles"]
    expires_at = None
    if body.expires_days:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=body.expires_days)).replace(tzinfo=None)
    t = ApiToken(
        name=body.name, token_hash=token_hash,
        username=username, roles=json.dumps(roles),
        expires_at=expires_at,
    )
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return {**_token_dict(t), "token": raw}  # raw token returned only this once

@router.get("/tokens")
async def list_api_tokens(user: dict = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(ApiToken).order_by(ApiToken.created_at.desc()))).scalars().all()
    return [_token_dict(r) for r in rows]

@router.delete("/tokens/{token_id}", status_code=204)
async def revoke_api_token(
    token_id: str,
    user: dict = Depends(require_admin),
    db:   AsyncSession = Depends(get_db),
):
    row = (await db.execute(select(ApiToken).where(ApiToken.id == token_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Token introuvable")
    row.enabled = False
    await db.commit()
