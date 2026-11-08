"""
Policy Engine + routes CRUD.

Les règles sont stockées en DB (table `policies`) — partagées entre tous les
workers uvicorn (l'ancien policies.json était local à chaque worker et sujet
aux écritures concurrentes). Chaque worker garde un cache compilé rafraîchi
toutes les POLICY_CACHE_TTL secondes.

Évaluation : deny > warn > allow, puis action par défaut.
En production, le défaut est DENY (allow-list) sauf si POLICY_PROD_DEFAULT=allow.
"""
import json, logging, os, re, time, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, func, select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import require_admin
from .db import Base, Session, get_db

log = logging.getLogger("policy")

# Migration : si un policies.json existe (ancien format), il est importé en DB
# au premier démarrage puis ignoré.
POLICY_FILE = Path("./policies.json")

# "deny"  → en production, toute commande ne matchant aucune règle `allow` est refusée (recommandé)
# "allow" → ancien comportement fail-open
POLICY_PROD_DEFAULT = os.getenv("POLICY_PROD_DEFAULT", "deny").lower()
POLICY_CACHE_TTL    = float(os.getenv("POLICY_CACHE_TTL", "10"))

_now = lambda: datetime.now(timezone.utc).replace(tzinfo=None)

DEFAULT_POLICIES = [
    {"id":"p001","name":"Block rm -rf",            "env":"production",  "pattern":r"rm\s+(-\w*\s+)*-\w*[rf]",           "action":"deny", "enabled":True,  "severity":"critical"},
    {"id":"p002","name":"Block sudo/su",            "env":"all",         "pattern":r"\b(sudo|su)\b",                     "action":"deny", "enabled":True,  "severity":"high"},
    {"id":"p003","name":"Block interactive shells", "env":"production",  "pattern":r"(bash|sh|zsh|fish)\s*$",            "action":"deny", "enabled":True,  "severity":"high"},
    {"id":"p004","name":"Block DB direct access",   "env":"production",  "pattern":r"(psql|mysql|mongosh|redis-cli)",    "action":"deny", "enabled":True,  "severity":"critical"},
    {"id":"p005","name":"Block curl|bash",          "env":"all",         "pattern":r"(curl|wget).+\|.*(bash|sh)",        "action":"deny", "enabled":True,  "severity":"critical"},
    {"id":"p006","name":"Block chmod 777",          "env":"all",         "pattern":r"chmod\s+(-\w+\s+)*777",             "action":"deny", "enabled":True,  "severity":"high"},
    {"id":"p007","name":"Warn find /",              "env":"production",  "pattern":r"find\s+/",                          "action":"warn", "enabled":True,  "severity":"medium"},
    {"id":"p008","name":"Allow read-only ops",      "env":"all",         "pattern":r"^(df|du|free|ps|cat|ls|tail|head|grep|uptime|uname|id|whoami|hostname|date|systemctl\s+status|journalctl)\b", "action":"allow","enabled":True,"severity":"info"},
    {"id":"p009","name":"Block reboot/shutdown",    "env":"all",         "pattern":r"\breboot\b|shutdown\b|\bpoweroff\b|\bhalt\b|systemctl\s+(reboot|poweroff|halt)|init\s+[06]\b|telinit\s+[06]\b", "action":"deny","enabled":True,"severity":"critical"},
]

class Policy(Base):
    __tablename__ = "policies"
    id         = Column(String, primary_key=True, default=lambda: f"p{uuid.uuid4().hex[:8]}")
    name       = Column(String, nullable=False)
    env        = Column(String, nullable=False, default="all")
    pattern    = Column(Text,   nullable=False)
    action     = Column(String, nullable=False, default="deny")   # deny | warn | allow
    severity   = Column(String, nullable=False, default="high")
    enabled    = Column(Boolean, default=True)
    triggers   = Column(Integer, default=0)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

_COLUMNS = {"id", "name", "env", "pattern", "action", "severity", "enabled", "triggers"}

def _dict(p: Policy) -> dict:
    return {
        "id": p.id, "name": p.name, "env": p.env, "pattern": p.pattern,
        "action": p.action, "severity": p.severity, "enabled": p.enabled,
        "triggers": p.triggers,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }

class PolicyEngine:
    def __init__(self):
        self._compiled: list[tuple[re.Pattern, dict]] = []
        self._loaded_at: float = 0.0

    async def load(self):
        """Au démarrage : seed la table si vide (depuis policies.json si présent,
        sinon les règles par défaut), puis charge le cache."""
        async with Session() as db:
            count = (await db.execute(select(func.count()).select_from(Policy))).scalar() or 0
            if count == 0:
                rules = DEFAULT_POLICIES
                if POLICY_FILE.exists():
                    try:
                        rules = json.loads(POLICY_FILE.read_text())
                        log.info("Import de %d règles depuis policies.json → DB", len(rules))
                    except Exception as e:
                        log.warning("policies.json illisible (%s) — règles par défaut", e)
                for r in rules:
                    db.add(Policy(**{k: v for k, v in r.items() if k in _COLUMNS}))
                try:
                    await db.commit()
                except Exception:
                    await db.rollback()  # course entre workers au premier démarrage — l'autre a seedé
        await self._refresh(force=True)
        log.info("Policy engine prêt — %d règles, prod_default=%s", len(self._compiled), POLICY_PROD_DEFAULT)

    async def _refresh(self, force: bool = False):
        if not force and time.time() - self._loaded_at < POLICY_CACHE_TTL:
            return
        async with Session() as db:
            rows = (await db.execute(select(Policy))).scalars().all()
        compiled = []
        for p in rows:
            try:
                compiled.append((re.compile(p.pattern, re.IGNORECASE), _dict(p)))
            except re.error as e:
                log.error("Regex invalide dans policy %s: %s", p.id, e)
        self._compiled = compiled
        self._loaded_at = time.time()

    def invalidate(self):
        """Force un rechargement au prochain evaluate (worker local uniquement —
        les autres workers se resynchronisent via le TTL)."""
        self._loaded_at = 0.0

    async def _bump(self, db: AsyncSession, pid: str):
        try:
            await db.execute(sa_update(Policy).where(Policy.id == pid).values(triggers=Policy.triggers + 1))
            await db.commit()
        except Exception as e:
            log.debug("Trigger bump %s: %s", pid, e)

    async def evaluate(self, ctx: dict, db: AsyncSession) -> dict:
        await self._refresh()
        env, command = ctx.get("host_env", ""), ctx.get("command", "")
        applicable = [(rx, r) for rx, r in self._compiled
                      if r["enabled"] and r["env"] in ("all", env)]

        # Priorité : deny > warn > allow — un deny ne peut pas être masqué par un allow.
        for rx, r in applicable:
            if r["action"] == "deny" and rx.search(command):
                await self._bump(db, r["id"])
                log.warning("DENY user=%s env=%s cmd=%r rule=%s", ctx.get("user"), env, command, r["id"])
                return {"action": "deny", "reason": f"[{r['name']}] commande non autorisée", "policy_id": r["id"]}

        for rx, r in applicable:
            if r["action"] == "warn" and rx.search(command):
                await self._bump(db, r["id"])
                log.warning("WARN user=%s env=%s cmd=%r rule=%s", ctx.get("user"), env, command, r["id"])

        for rx, r in applicable:
            if r["action"] == "allow" and rx.search(command):
                await self._bump(db, r["id"])
                return {"action": "allow", "reason": None, "policy_id": r["id"]}

        # Action par défaut : allow-list en production
        if env == "production" and POLICY_PROD_DEFAULT == "deny":
            log.warning("DEFAULT-DENY user=%s env=production cmd=%r", ctx.get("user"), command)
            return {"action": "deny",
                    "reason": "Production en mode allow-list : aucune règle 'allow' ne correspond à cette commande",
                    "policy_id": "default-deny"}
        return {"action": "allow", "reason": None, "policy_id": None}

# ── Routes ────────────────────────────────────────────────────────────────────
router = APIRouter(prefix="/policies", tags=["policies"])

class PolicyCreate(BaseModel):
    name: str
    env: str = "all"
    pattern: str
    action: str = "deny"
    severity: str = "high"
    enabled: bool = True

class PolicyUpdate(BaseModel):
    name: Optional[str] = None
    env: Optional[str] = None
    pattern: Optional[str] = None
    action: Optional[str] = None
    severity: Optional[str] = None
    enabled: Optional[bool] = None

def _engine(request: Request) -> PolicyEngine:
    return request.app.state.policies

def _validate(pattern: Optional[str], action: Optional[str]):
    if pattern is not None:
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            raise HTTPException(422, f"Regex invalide: {e}")
    if action is not None and action not in ("deny", "warn", "allow"):
        raise HTTPException(422, "action doit être deny, warn ou allow")

# Lecture réservée aux admins : les patterns de deny révèlent comment les contourner.
@router.get("")
async def list_policies(user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Policy).order_by(Policy.created_at))).scalars().all()
    return [_dict(r) for r in rows]

@router.post("", status_code=201)
async def create_policy(body: PolicyCreate, request: Request, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    _validate(body.pattern, body.action)
    p = Policy(**body.model_dump())
    db.add(p)
    await db.commit()
    await db.refresh(p)
    _engine(request).invalidate()
    return _dict(p)

@router.put("/{pid}")
async def update_policy(pid: str, body: PolicyUpdate, request: Request, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    _validate(body.pattern, body.action)
    p = (await db.execute(select(Policy).where(Policy.id == pid))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, f"Policy {pid} introuvable")
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(p, k, v)
    await db.commit()
    await db.refresh(p)
    _engine(request).invalidate()
    return _dict(p)

@router.delete("/{pid}")
async def delete_policy(pid: str, request: Request, user=Depends(require_admin), db: AsyncSession = Depends(get_db)):
    p = (await db.execute(select(Policy).where(Policy.id == pid))).scalar_one_or_none()
    if not p:
        raise HTTPException(404)
    await db.delete(p)
    await db.commit()
    _engine(request).invalidate()
    return {"deleted": True}
