"""
Policy Engine OPA-compatible + routes CRUD
"""
import json, logging, re, uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .auth import current_user, require_admin

log = logging.getLogger("policy")
POLICY_FILE = Path("./policies.json")

DEFAULT_POLICIES = [
    {"id":"p001","name":"Block rm -rf",            "env":"production",  "pattern":r"rm\s+-rf",                          "action":"deny", "enabled":True,  "severity":"critical","triggers":12},
    {"id":"p002","name":"Block sudo",               "env":"all",         "pattern":r"sudo\s+",                           "action":"deny", "enabled":True,  "severity":"high",    "triggers":34},
    {"id":"p003","name":"Block interactive shells", "env":"production",  "pattern":r"(bash|sh|zsh|fish)\s*$",            "action":"deny", "enabled":True,  "severity":"high",    "triggers":8},
    {"id":"p004","name":"Block DB direct access",   "env":"production",  "pattern":r"(psql|mysql|mongosh|redis-cli)",    "action":"deny", "enabled":True,  "severity":"critical","triggers":5},
    {"id":"p005","name":"Block curl|bash",          "env":"all",         "pattern":r"curl.+\|.*(bash|sh)",               "action":"deny", "enabled":True,  "severity":"critical","triggers":3},
    {"id":"p006","name":"Block chmod 777",          "env":"all",         "pattern":r"chmod\s+777",                       "action":"deny", "enabled":True,  "severity":"high",    "triggers":2},
    {"id":"p007","name":"Warn find /",              "env":"production",  "pattern":r"find\s+/",                          "action":"warn", "enabled":True,  "severity":"medium",  "triggers":2},
    {"id":"p008","name":"Allow read-only ops",      "env":"all",         "pattern":r"^(df|du|free|ps|cat|ls|tail|grep|uptime|uname|id|whoami|hostname|date)", "action":"allow","enabled":True,"severity":"info","triggers":456},
    {"id":"p009","name":"Block reboot/shutdown",    "env":"all",         "pattern":r"\breboot\b|shutdown\b|\bpoweroff\b|\bhalt\b|systemctl\s+(reboot|poweroff|halt)|init\s+[06]\b|telinit\s+[06]\b", "action":"deny","enabled":True,"severity":"critical","triggers":0},
]

class PolicyEngine:
    def __init__(self):
        self._rules: list[dict] = []
        self._compiled: list[tuple] = []  # (re.Pattern, rule_dict)

    def _compile(self):
        result = []
        for rule in self._rules:
            try:
                result.append((re.compile(rule["pattern"], re.IGNORECASE), rule))
            except re.error as e:
                log.error("Invalid regex in policy %s: %s", rule["id"], e)
        self._compiled = result

    async def load(self):
        if POLICY_FILE.exists():
            try:
                self._rules = json.loads(POLICY_FILE.read_text())
                self._compile()
                log.info("Loaded %d policies", len(self._rules))
                return
            except Exception as e:
                log.warning("Policy file error: %s — using defaults", e)
        self._rules = [r.copy() for r in DEFAULT_POLICIES]
        self._compile()

    async def _save(self):
        POLICY_FILE.write_text(json.dumps(self._rules, indent=2))

    def get_all(self) -> list[dict]:
        return self._rules

    async def evaluate(self, ctx: dict) -> dict:
        env, command = ctx.get("host_env",""), ctx.get("command","")
        for pattern, rule in self._compiled:
            if not rule.get("enabled", True):
                continue
            if rule.get("env","all") not in ("all", env):
                continue
            if pattern.search(command):
                action = rule.get("action","deny")
                rule["triggers"] = rule.get("triggers",0) + 1
                await self._save()
                if action == "deny":
                    log.warning("DENY user=%s env=%s cmd=%r rule=%s", ctx.get("user"), env, command, rule["id"])
                    return {"action":"deny","reason":f"[{rule['name']}] commande non autorisée","policy_id":rule["id"]}
                elif action == "warn":
                    log.warning("WARN user=%s env=%s cmd=%r rule=%s", ctx.get("user"), env, command, rule["id"])
        return {"action":"allow","reason":None,"policy_id":None}

    async def create(self, data: dict) -> dict:
        data["id"] = data.get("id") or f"p{str(uuid.uuid4())[:8]}"
        data.setdefault("triggers", 0)
        self._rules.append(data)
        self._compile()
        await self._save()
        return data

    async def update(self, rule_id: str, data: dict) -> Optional[dict]:
        for i, r in enumerate(self._rules):
            if r["id"] == rule_id:
                self._rules[i] = {**r, **data, "id": rule_id}
                self._compile()
                await self._save()
                return self._rules[i]
        return None

    async def delete(self, rule_id: str) -> bool:
        before = len(self._rules)
        self._rules = [r for r in self._rules if r["id"] != rule_id]
        if len(self._rules) < before:
            self._compile()
            await self._save()
            return True
        return False

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

def _engine(request) -> PolicyEngine:
    return request.app.state.policies

from fastapi import Request

@router.get("")
async def list_policies(request: Request, user=Depends(current_user)):
    return _engine(request).get_all()

@router.post("", status_code=201)
async def create_policy(body: PolicyCreate, request: Request, user=Depends(require_admin)):
    return await _engine(request).create(body.model_dump())

@router.put("/{pid}")
async def update_policy(pid: str, body: PolicyUpdate, request: Request, user=Depends(require_admin)):
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    result = await _engine(request).update(pid, data)
    if not result:
        raise HTTPException(404, f"Policy {pid} introuvable")
    return result

@router.delete("/{pid}")
async def delete_policy(pid: str, request: Request, user=Depends(require_admin)):
    if not await _engine(request).delete(pid):
        raise HTTPException(404)
    return {"deleted": True}
