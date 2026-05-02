"""
MCP SSH Gateway — Serveur stdio pour Claude Code
À enregistrer dans claude_desktop_config.json comme process local
Communique avec le gateway HTTP pour exécuter les commandes SSH
"""
import asyncio, json, logging, os, sys
from typing import Any

import httpx

log = logging.getLogger("mcp-ssh-stdio")
GATEWAY_URL = os.getenv("MCP_SSH_GATEWAY_URL", "http://localhost:8080")
GATEWAY_TOKEN = os.getenv("MCP_SSH_TOKEN", "")

async def _fetch_token() -> str:
    user = os.getenv("MCP_SSH_USER", "")
    pwd  = os.getenv("MCP_SSH_PASS", "")
    if not user or not pwd:
        raise RuntimeError("MCP_SSH_TOKEN ou MCP_SSH_USER+MCP_SSH_PASS requis")
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=10) as c:
        r = await c.post("/auth/token", json={"username": user, "password": pwd})
        r.raise_for_status()
        return r.json()["access_token"]

async def ensure_token() -> str:
    global GATEWAY_TOKEN
    if not GATEWAY_TOKEN:
        GATEWAY_TOKEN = await _fetch_token()
    return GATEWAY_TOKEN

# ── MCP Protocol helpers ──────────────────────────────────────────────────────
def _msg(id_: Any, result: Any = None, error: Any = None) -> dict:
    m: dict = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        m["error"] = {"code": -32000, "message": str(error)}
    else:
        m["result"] = result
    return m

def _write(msg: dict):
    line = json.dumps(msg, ensure_ascii=False) + "\n"
    sys.stdout.write(line)
    sys.stdout.flush()

# ── Tool definitions ──────────────────────────────────────────────────────────
TOOLS = [
    {
        "name": "ssh_exec",
        "description": "Exécute une commande non-interactive sur un hôte SSH via le gateway enterprise. Supporte tous les hôtes du registre selon vos droits. Toute commande est auditée et soumise aux politiques OPA.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "host":    {"type": "string", "description": "Nom de l'hôte (ex: prod-web-01) ou IP"},
                "command": {"type": "string", "description": "Commande shell non-interactive"},
                "timeout": {"type": "integer", "description": "Timeout en secondes (défaut: 60)", "default": 60},
            },
            "required": ["host", "command"],
        },
    },
    {
        "name": "ssh_list_hosts",
        "description": "Liste les hôtes SSH disponibles selon vos droits (filtrés par environnement et rôles).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "env": {"type": "string", "description": "Filtrer par env: production, staging, development", "enum": ["production", "staging", "development"]},
            },
        },
    },
    {
        "name": "ssh_audit",
        "description": "Consulte le log d'audit des commandes SSH récentes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit":  {"type": "integer", "description": "Nombre max de résultats (défaut: 20)", "default": 20},
                "env":    {"type": "string", "description": "Filtrer par environnement"},
                "status": {"type": "string", "description": "Filtrer par statut: allowed, denied", "enum": ["allowed", "denied"]},
            },
        },
    },
]

# ── HTTP client ───────────────────────────────────────────────────────────────
async def call_gateway(method: str, path: str, **kwargs) -> Any:
    token = await ensure_token()
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(base_url=GATEWAY_URL, timeout=90) as client:
        resp = await getattr(client, method)(path, headers=headers, **kwargs)
        resp.raise_for_status()
        return resp.json()

# ── Tool handlers ─────────────────────────────────────────────────────────────
async def handle_ssh_exec(args: dict) -> str:
    host = args.get("host", "")
    command = args.get("command", "")
    timeout = args.get("timeout", 60)

    result = await call_gateway("post", "/exec", json={"host": host, "command": command, "timeout": timeout})

    if result["status"] == "denied":
        return f"🚫 REFUSÉ — {result.get('reason', 'Policy OPA')}"
    if result["status"] == "error":
        return f"❌ ERREUR — {result.get('reason', 'SSH error')}"

    out = []
    if result.get("stdout"):
        out.append(result["stdout"].rstrip())
    if result.get("stderr"):
        out.append(f"[stderr] {result['stderr'].rstrip()}")
    out.append(f"\n[exit={result.get('exit_code', '?')} | {result.get('duration_ms', '?')}ms | hash={result.get('stdout_hash', '')[:12]}…]")
    return "\n".join(out) if out else "(empty output)"

async def handle_list_hosts(args: dict) -> str:
    hosts = await call_gateway("get", "/hosts")
    env_filter = args.get("env")
    if env_filter:
        hosts = [h for h in hosts if h.get("env") == env_filter]
    if not hosts:
        return "Aucun hôte disponible pour vos droits."
    lines = ["| Nom | Env | Hostname | Rôles |", "|-----|-----|----------|-------|"]
    for h in hosts:
        lines.append(f"| {h['name']} | {h['env']} | {h['hostname']} | {h['allowed_roles']} |")
    return "\n".join(lines)

async def handle_audit(args: dict) -> str:
    params = {k: v for k, v in args.items() if v is not None}
    params.setdefault("limit", 20)
    logs = await call_gateway("get", "/audit", params=params)
    if not logs:
        return "Aucune entrée dans l'audit log."
    lines = []
    for e in logs:
        icon = "✅" if e["status"] == "allowed" else "🚫"
        lines.append(f"{icon} [{e['timestamp']}] {e['user']}@{e['host_name']} » {e['command'][:60]}")
        if e.get("deny_reason"):
            lines.append(f"   └ Raison: {e['deny_reason']}")
    return "\n".join(lines)

TOOL_HANDLERS = {
    "ssh_exec": handle_ssh_exec,
    "ssh_list_hosts": handle_list_hosts,
    "ssh_audit": handle_audit,
}

# ── MCP message dispatcher ────────────────────────────────────────────────────
async def dispatch(msg: dict):
    method = msg.get("method", "")
    id_ = msg.get("id")
    params = msg.get("params", {})

    if method == "initialize":
        _write(_msg(id_, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mcp-ssh-gateway", "version": "2.0.0"},
        }))

    elif method == "tools/list":
        _write(_msg(id_, {"tools": TOOLS}))

    elif method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})
        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            _write(_msg(id_, error=f"Unknown tool: {tool_name}"))
            return
        try:
            result_text = await handler(tool_args)
            _write(_msg(id_, {"content": [{"type": "text", "text": result_text}]}))
        except httpx.HTTPStatusError as e:
            _write(_msg(id_, error=f"Gateway HTTP {e.response.status_code}: {e.response.text[:200]}"))
        except Exception as e:
            log.error("Tool error: %s", e, exc_info=True)
            _write(_msg(id_, error=str(e)))

    elif method == "notifications/initialized":
        pass  # no-op

    else:
        if id_ is not None:
            _write(_msg(id_, error=f"Method not found: {method}"))

# ── Main loop ─────────────────────────────────────────────────────────────────
async def main():
    log.info("MCP SSH Gateway stdio server started — gateway=%s", GATEWAY_URL)
    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    while True:
        try:
            line = await reader.readline()
            if not line:
                break
            line = line.decode().strip()
            if not line:
                continue
            msg = json.loads(line)
            await dispatch(msg)
        except json.JSONDecodeError as e:
            log.warning("JSON decode error: %s", e)
        except Exception as e:
            log.error("Dispatch error: %s", e, exc_info=True)

if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    asyncio.run(main())
