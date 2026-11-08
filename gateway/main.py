"""
MCP SSH Gateway — FastAPI backend complet
Routes: auth, hosts CRUD, exec SSH, policies CRUD, audit, metrics summary, health
"""
import asyncio, hashlib, logging, os, time, uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

import asyncssh
import redis.asyncio as redis
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel, Field as PydField
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, event as sa_event, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import FileResponse, Response

from .auth import current_user, require_admin, router as auth_router
from .cyberark_ssh import CyberArkSSHClient, test_cyberark_connection
from .db import Base, engine, Session, get_db
from .policy import PolicyEngine, router as policy_router
from .vault_ssh import VaultSSHClient

log = logging.getLogger("gateway")

RATE_LIMITS = {
    "production":  int(os.getenv("RATE_PROD", "10")),
    "staging":     int(os.getenv("RATE_STG",  "30")),
    "development": int(os.getenv("RATE_DEV",  "100")),
}
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Politique de vérification des clés d'hôte SSH :
#   strict   → host_key requis en DB, sinon la commande est refusée (défaut)
#   tofu     → Trust On First Use : la clé est récupérée et épinglée au premier exec
#   insecure → aucune vérification (MITM possible — dev/local uniquement)
SSH_HOST_KEY_POLICY = os.getenv("SSH_HOST_KEY_POLICY", "strict").lower()

class Host(Base):
    __tablename__ = "hosts"
    id            = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name          = Column(String, nullable=False, unique=True)
    hostname      = Column(String, nullable=False)
    port          = Column(Integer, default=22)
    ssh_user      = Column(String, nullable=False, default="deploy")
    env           = Column(String, nullable=False, default="development")
    vault_role    = Column(String, nullable=False, default="dev-ssh")
    allowed_roles = Column(String, nullable=False, default="dev")
    host_key      = Column(Text)   # clé publique SSH de l'hôte (format authorized_keys) — épinglage
    enabled       = Column(Boolean, default=True)
    created_at    = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    updated_at    = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
                           onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id  = Column(String)
    timestamp   = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    user        = Column(String, nullable=False)
    host_name   = Column(String, nullable=False)
    host_env    = Column(String, nullable=False)
    command     = Column(Text, nullable=False)
    status      = Column(String, nullable=False)  # allowed | denied | error
    deny_reason = Column(Text)
    stdout_hash = Column(String)
    exit_code   = Column(Integer)
    duration_ms = Column(Integer)
    policy_id   = Column(String)

# ── Metrics ───────────────────────────────────────────────────────────────────
CMD_TOTAL        = Counter("mcp_ssh_commands_total",          "Commands total",              ["env","status"])
CMD_DUR          = Histogram("mcp_ssh_command_duration_sec",  "Command duration",            ["env"])
SESS_GAUGE       = Gauge("mcp_ssh_active_sessions",           "Active sessions")
DENY_CNT         = Counter("mcp_ssh_denials_total",           "Denials by policy",           ["env","policy"])
DB_POOL_CHECKEDOUT = Gauge("db_pool_checked_out",             "DB connections checked out")

# ── Rate limiter ──────────────────────────────────────────────────────────────
# Primary: Redis sliding window (shared across all workers).
# Fallback: in-process dict used when Redis is unavailable.
_rl: dict[str, list[float]] = {}

async def _rate_ok(redis_client, key: str, env: str) -> bool:
    limit = RATE_LIMITS.get(env, 30)
    now   = time.time()
    try:
        pipe = redis_client.pipeline()
        pipe.zremrangebyscore(key, 0, now - 60)
        pipe.zcard(key)
        pipe.zadd(key, {str(now): now})
        pipe.expire(key, 120)
        results = await pipe.execute()
        return results[1] < limit          # count BEFORE the new zadd
    except Exception:
        # Redis unavailable — fall back to per-worker in-memory limiter
        _rl[key] = [t for t in _rl.get(key, []) if now - t < 60]
        if len(_rl[key]) >= limit:
            return False
        _rl[key].append(now)
        return True

# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as e:
        # Concurrent worker race: the other worker already created the schema
        if "already exists" not in str(e).lower() and "unique" not in str(e).lower():
            raise
        log.warning("DB schema already exists (concurrent startup) — continuing")

    # Migration légère : ajoute hosts.host_key sur les bases existantes
    try:
        async with engine.begin() as conn:
            await conn.exec_driver_sql("ALTER TABLE hosts ADD COLUMN host_key TEXT")
    except Exception:
        pass  # colonne déjà présente

    # DB pool metrics via SQLAlchemy events (no-op on SQLite which has no pool)
    try:
        @sa_event.listens_for(engine.sync_engine, "checkout")
        def _pool_checkout(dbapi_conn, conn_rec, conn_proxy):
            DB_POOL_CHECKEDOUT.inc()

        @sa_event.listens_for(engine.sync_engine, "checkin")
        def _pool_checkin(dbapi_conn, conn_rec):
            DB_POOL_CHECKEDOUT.dec()
    except Exception:
        pass

    app.state.redis    = redis.from_url(REDIS_URL, decode_responses=True)
    app.state.vault    = CyberArkSSHClient() if os.getenv("SSH_MODE", "key").startswith("cyberark") else VaultSSHClient()
    app.state.policies = PolicyEngine()
    await app.state.policies.load()
    log.info("Gateway ready — DB=%s SSH_MODE=%s REDIS=%s",
             os.getenv("DATABASE_URL","sqlite"), os.getenv("SSH_MODE","key"), REDIS_URL)
    yield
    await engine.dispose()
    await app.state.redis.aclose()
    if hasattr(app.state.vault, "close"):
        await app.state.vault.close()

app = FastAPI(title="MCP SSH Gateway", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = rid
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response
app.include_router(auth_router)
app.include_router(policy_router)

# ── Pydantic ──────────────────────────────────────────────────────────────────
class HostBody(BaseModel):
    name:          str
    hostname:      str
    port:          int    = 22
    ssh_user:      str    = "deploy"
    env:           str    = "development"
    vault_role:    str    = "dev-ssh"
    allowed_roles: str    = "dev"
    host_key:      Optional[str] = None
    enabled:       bool   = True

class ExecReq(BaseModel):
    host:       str
    command:    str = PydField(max_length=8192)
    timeout:    int = PydField(default=60, ge=1, le=600)
    session_id: Optional[str] = None

# ── Host helpers ──────────────────────────────────────────────────────────────
def _host_dict(h: Host) -> dict:
    return {
        "id": h.id, "name": h.name, "hostname": h.hostname, "port": h.port,
        "ssh_user": h.ssh_user, "env": h.env, "vault_role": h.vault_role,
        "allowed_roles": h.allowed_roles, "host_key": h.host_key, "enabled": h.enabled,
        "created_at": h.created_at.isoformat() if h.created_at else None,
    }

# ── Routes: Hosts ─────────────────────────────────────────────────────────────
@app.get("/hosts")
async def list_hosts(user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    user_roles = set(user["roles"])
    rows = (await db.execute(select(Host).where(Host.enabled == True))).scalars().all()
    # Admins see all; others see only hosts matching their roles
    if "admin" in user_roles:
        return [_host_dict(h) for h in rows]
    return [_host_dict(h) for h in rows if user_roles & set(h.allowed_roles.split(","))]

@app.post("/hosts", status_code=201)
async def create_host(body: HostBody, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    existing = (await db.execute(select(Host).where(Host.name == body.name))).scalar_one_or_none()
    if existing:
        raise HTTPException(409, f"Hôte '{body.name}' déjà existant")
    host = Host(**body.model_dump())
    db.add(host)
    await db.commit()
    await db.refresh(host)
    return _host_dict(host)

@app.put("/hosts/{host_id}")
async def update_host(host_id: str, body: HostBody, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    host = (await db.execute(select(Host).where(Host.id == host_id))).scalar_one_or_none()
    if not host:
        raise HTTPException(404)
    # exclude_unset : ne pas écraser host_key (épinglée via TOFU) si le client ne l'envoie pas
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(host, k, v)
    host.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    await db.refresh(host)
    return _host_dict(host)

@app.delete("/hosts/{host_id}")
async def delete_host(host_id: str, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    host = (await db.execute(select(Host).where(Host.id == host_id))).scalar_one_or_none()
    if not host:
        raise HTTPException(404)
    host.enabled = False
    await db.commit()
    return {"deleted": True}

# ── Host key verification ─────────────────────────────────────────────────────
def _known_hosts_data(hostname: str, port: int, pubkey: str) -> str:
    """Construit une entrée au format known_hosts pour l'hôte épinglé."""
    name = hostname if port == 22 else f"[{hostname}]:{port}"
    return f"{name} {pubkey.strip()}\n"

async def _resolve_known_hosts(host: Host, db: AsyncSession):
    """Retourne l'objet known_hosts pour asyncssh selon SSH_HOST_KEY_POLICY."""
    if SSH_HOST_KEY_POLICY == "insecure":
        log.warning("SSH_HOST_KEY_POLICY=insecure — clé d'hôte de '%s' NON vérifiée", host.name)
        return None
    if not host.host_key:
        if SSH_HOST_KEY_POLICY == "tofu":
            key = await asyncssh.get_server_host_key(
                host.hostname, port=host.port,
                options=asyncssh.SSHClientConnectionOptions(known_hosts=None, connect_timeout=10),
            )
            pub = key.export_public_key().decode().strip()
            host.host_key = pub
            await db.commit()
            log.warning("TOFU: clé d'hôte de '%s' épinglée (%s)", host.name, pub.split()[0])
        else:  # strict
            raise HTTPException(
                428,
                f"Clé d'hôte inconnue pour '{host.name}'. La renseigner via PUT /hosts/{host.id} "
                "(champ host_key, format authorized_keys), ou définir SSH_HOST_KEY_POLICY=tofu.",
            )
    return asyncssh.import_known_hosts(_known_hosts_data(host.hostname, host.port, host.host_key))

# ── Route: Exec ───────────────────────────────────────────────────────────────
@app.post("/exec")
async def exec_cmd(body: ExecReq, req: Request, user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    host = (await db.execute(
        select(Host).where(Host.name == body.host, Host.enabled == True)
    )).scalar_one_or_none()
    if not host:
        raise HTTPException(404, f"Hôte '{body.host}' inconnu ou désactivé")

    username   = user["sub"]
    user_roles = set(user["roles"])
    sid        = body.session_id or str(uuid.uuid4())
    start      = int(time.time() * 1000)

    # 1. RBAC
    allowed_roles = set(host.allowed_roles.split(","))
    if "admin" not in user_roles and not (user_roles & allowed_roles):
        await _audit(db, sid, username, host, body.command, "denied", "Rôle insuffisant", None, None, 0, None)
        return {"status": "denied", "reason": f"Rôle requis: {host.allowed_roles}"}

    # 2. Rate limit
    if not await _rate_ok(req.app.state.redis, f"rl:{username}:{host.name}", host.env):
        return {"status": "denied", "reason": f"Rate limit: max {RATE_LIMITS.get(host.env,30)} cmd/min en {host.env}"}

    # 3. Policy
    pol = await req.app.state.policies.evaluate({"host_env": host.env, "command": body.command, "user": username, "roles": list(user_roles)})
    if pol["action"] == "deny":
        await _audit(db, sid, username, host, body.command, "denied", pol["reason"], None, None, 0, pol.get("policy_id"))
        DENY_CNT.labels(env=host.env, policy=pol.get("policy_id","?")).inc()
        CMD_TOTAL.labels(env=host.env, status="denied").inc()
        return {"status": "denied", "reason": pol["reason"]}

    # 4. SSH credential (Vault cert, Vault KV, clé statique ou CyberArk)
    try:
        key_path, cert_path, passphrase, password = await req.app.state.vault.get_credential(host.vault_role, username)
    except Exception as e:
        raise HTTPException(500, f"Credential SSH error: {e}")

    # 5. Host key (épinglage anti-MITM) puis exec
    try:
        known_hosts = await _resolve_known_hosts(host, db)
    except HTTPException:
        raise
    except Exception as e:
        log.error("Host key %s: %s", host.name, e)
        CMD_TOTAL.labels(env=host.env, status="error").inc()
        return {"status": "error", "reason": f"Clé d'hôte irrécupérable pour '{host.name}': {e}"}

    SESS_GAUGE.inc()
    stdout = stderr = ""; exit_code = -1
    try:
        conn_kwargs = dict(
            host=host.hostname, port=host.port, username=host.ssh_user,
            known_hosts=known_hosts, connect_timeout=10,
        )
        if key_path:
            conn_kwargs["client_keys"] = [key_path]
            if cert_path:
                conn_kwargs["cert_file"] = cert_path
            if passphrase:
                conn_kwargs["passphrase"] = passphrase
        elif password:
            conn_kwargs["password"] = password
            conn_kwargs["preferred_auth"] = ["password"]

        async with asyncssh.connect(**conn_kwargs) as conn:
            result = await asyncio.wait_for(conn.run(body.command, check=False), timeout=body.timeout)
            stdout    = result.stdout or ""
            stderr    = result.stderr or ""
            exit_code = result.exit_status or 0
    except asyncio.TimeoutError:
        CMD_TOTAL.labels(env=host.env, status="error").inc()
        return {"status": "error", "reason": f"Timeout {body.timeout}s"}
    except Exception as e:
        log.error("SSH exec %s: %s", host.name, e)
        CMD_TOTAL.labels(env=host.env, status="error").inc()
        return {"status": "error", "reason": str(e)}
    finally:
        SESS_GAUGE.dec()
        import os as _os, tempfile as _tmp
        for p in [key_path, cert_path]:
            if p and _os.path.exists(p) and p.startswith(_tmp.gettempdir()):
                _os.unlink(p)

    ms   = int(time.time() * 1000) - start
    h256 = hashlib.sha256(stdout.encode()).hexdigest()
    await _audit(db, sid, username, host, body.command, "allowed", None, h256, exit_code, ms, None)
    CMD_TOTAL.labels(env=host.env, status="allowed").inc()
    CMD_DUR.labels(env=host.env).observe(ms / 1000)
    return {"status":"allowed","stdout":stdout,"stderr":stderr,"exit_code":exit_code,"duration_ms":ms,"stdout_hash":h256,"session_id":sid}

async def _audit(db, sid, user, host, cmd, status, reason, h256, exit_code, ms, pol_id):
    db.add(AuditLog(session_id=sid, user=user, host_name=host.name, host_env=host.env,
                    command=cmd, status=status, deny_reason=reason, stdout_hash=h256,
                    exit_code=exit_code, duration_ms=ms, policy_id=pol_id))
    await db.commit()

# ── Route: Audit ──────────────────────────────────────────────────────────────
@app.get("/audit")
async def get_audit(
    limit: int = 50, env: Optional[str] = None, status: Optional[str] = None,
    user_filter: Optional[str] = None,
    user=Depends(current_user), db: AsyncSession = Depends(get_db),
):
    q = select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(min(limit, 500))
    if env:         q = q.where(AuditLog.host_env == env)
    if status:      q = q.where(AuditLog.status == status)
    if user_filter: q = q.where(AuditLog.user == user_filter)
    rows = (await db.execute(q)).scalars().all()
    return [{
        "id": r.id, "session_id": r.session_id,
        "timestamp": r.timestamp.isoformat() if r.timestamp else None,
        "user": r.user, "host_name": r.host_name, "host_env": r.host_env,
        "command": r.command, "status": r.status, "deny_reason": r.deny_reason,
        "stdout_hash": r.stdout_hash, "exit_code": r.exit_code,
        "duration_ms": r.duration_ms, "policy_id": r.policy_id,
    } for r in rows]

# ── Route: Metrics summary (dashboard) ───────────────────────────────────────
@app.get("/metrics/summary")
async def metrics_summary(user=Depends(current_user), db: AsyncSession = Depends(get_db)):
    now_ts   = datetime.now(timezone.utc).replace(tzinfo=None)
    since_24 = now_ts - timedelta(hours=24)

    # Single query for all audit data in the last 24h — aggregated in Python.
    # Replaces the previous 48 sequential DB round-trips.
    rows = (await db.execute(
        select(AuditLog.timestamp, AuditLog.status, AuditLog.host_env)
        .where(AuditLog.timestamp >= since_24)
    )).all()

    total_24h = denied_24h = allowed_24h = 0
    env_counts = {"production": 0, "staging": 0, "development": 0}
    hourly = [
        {"t": (now_ts - timedelta(hours=h)).strftime("%H:%M"), "commands": 0, "denied": 0}
        for h in range(23, -1, -1)
    ]

    for ts, status, env in rows:
        total_24h += 1
        if status == "denied":
            denied_24h += 1
        elif status == "allowed":
            allowed_24h += 1
        if env in env_counts:
            env_counts[env] += 1
        if ts:
            hours_ago = int((now_ts - ts).total_seconds() / 3600)
            if 0 <= hours_ago < 24:
                idx = 23 - hours_ago
                hourly[idx]["commands"] += 1
                if status == "denied":
                    hourly[idx]["denied"] += 1

    total_hosts = (await db.execute(
        select(func.count()).select_from(Host).where(Host.enabled == True)
    )).scalar() or 0

    return {
        "total_24h": total_24h, "denied_24h": denied_24h,
        "allowed_24h": allowed_24h, "total_hosts": total_hosts,
        "env_breakdown": env_counts, "hourly": hourly,
    }

# ── Route: Prometheus + health ────────────────────────────────────────────────
@app.get("/metrics/prometheus")
async def prom_metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/health")
async def health(req: Request):
    vault_status = await req.app.state.vault.verify()
    return {"status":"ok","version":"2.0.0","ssh_mode":os.getenv("SSH_MODE","key"),"vault":vault_status,"ts":datetime.now(timezone.utc).isoformat()}

# ── Routes: Vault KV credentials ─────────────────────────────────────────────
class VaultCredentials(BaseModel):
    vault_role:  str
    private_key: str
    passphrase:  Optional[str] = None

def _require_vault_kv():
    if os.getenv("SSH_MODE", "key") != "vault-kv":
        raise HTTPException(400, "Disponible uniquement en mode vault-kv")

@app.post("/vault/credentials", status_code=201)
async def store_vault_credentials(body: VaultCredentials, req: Request, user=Depends(require_admin)):
    _require_vault_kv()
    try:
        await req.app.state.vault.store_ssh_key(body.vault_role, body.private_key, body.passphrase)
        return {"stored": True, "vault_role": body.vault_role}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/vault/credentials/status")
async def vault_credentials_status(vault_role: str, req: Request, user=Depends(current_user)):
    _require_vault_kv()
    try:
        return await req.app.state.vault.get_kv_status(vault_role)
    except Exception as e:
        raise HTTPException(500, str(e))

@app.delete("/vault/credentials")
async def delete_vault_credentials(vault_role: str, req: Request, user=Depends(require_admin)):
    _require_vault_kv()
    try:
        await req.app.state.vault.delete_ssh_key(vault_role)
        return {"deleted": True, "vault_role": vault_role}
    except Exception as e:
        raise HTTPException(500, str(e))

# ── Routes: CyberArk PAM ──────────────────────────────────────────────────────
class CyberArkTestRequest(BaseModel):
    url:        str
    username:   str
    password:   str
    safe:       str
    object_name: str
    verify_ssl: bool = True

@app.post("/cyberark/test")
async def cyberark_test(body: CyberArkTestRequest, user=Depends(require_admin)):
    result = await test_cyberark_connection(
        body.url, body.username, body.password,
        body.safe, body.object_name, body.verify_ssl,
    )
    return result

@app.get("/cyberark/status")
async def cyberark_status(req: Request, user=Depends(current_user)):
    if os.getenv("SSH_MODE") == "cyberark":
        return await req.app.state.vault.verify()
    return {"mode": "cyberark", "ok": False, "configured": False}

# ── Static UI (SPA) ───────────────────────────────────────────────────────────
_static = Path("/app/static")
if _static.exists():
    _assets = _static / "assets"
    if _assets.exists():
        app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")

    _static_root = _static.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        try:
            file = (_static / full_path).resolve()
        except (OSError, ValueError):
            raise HTTPException(404)
        # Anti path-traversal : ne servir que des fichiers sous /app/static
        if not file.is_relative_to(_static_root):
            raise HTTPException(404)
        if file.is_file():
            return FileResponse(str(file))
        return FileResponse(str(_static_root / "index.html"))
