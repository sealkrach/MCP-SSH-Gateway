"""
Vault SSH Secrets Engine + Vault KV + fallback clé statique pour dev
MODE=key       → clé privée statique (dev/test)
MODE=vault     → certificats éphémères signés par Vault SSH CA (prod)
MODE=vault-kv  → clé privée stockée dans Vault KV (clé uploadée via UI)

Les clés privées sont manipulées EN MÉMOIRE (objets asyncssh.SSHKey) —
jamais écrites sur disque, sauf en mode `key` où la clé est déjà un fichier.

get_credential() retourne (client_keys, password) :
  client_keys → liste pour asyncssh.connect(client_keys=...) : SSHKey,
                tuple (SSHKey, SSHCertificate) ou chemin de fichier
  password    → authentification par mot de passe (CyberArk)
"""
import asyncssh, logging, os, time
from pathlib import Path
from typing import Optional, Tuple

import httpx

log = logging.getLogger("vault-ssh")

VAULT_URL      = os.getenv("VAULT_URL", "http://localhost:8200")
VAULT_TOKEN    = os.getenv("VAULT_TOKEN", "")
VAULT_MOUNT    = os.getenv("VAULT_MOUNT", "ssh")
VAULT_KV_MOUNT = os.getenv("VAULT_KV_MOUNT", "secret")
VAULT_KV_PATH  = os.getenv("VAULT_KV_PATH", "mcp-gateway/ssh-key")
SSH_MODE       = os.getenv("SSH_MODE", "key")
SSH_KEY        = os.getenv("SSH_KEY_PATH", "~/.ssh/id_rsa")
SSH_TTL        = os.getenv("VAULT_SSH_TTL", "5m")

# Cache in-process de la clé Vault KV (objet SSHKey déjà déchiffré).
_KV_CACHE: dict[str, tuple[object, float]] = {}  # cache_key → (SSHKey, fetched_at)
_KV_CACHE_TTL = 300  # 5 minutes


class VaultSSHClient:

    def __init__(self):
        self._http: Optional[httpx.AsyncClient] = None

    def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=VAULT_URL,
                headers={"X-Vault-Token": VAULT_TOKEN},
                timeout=10.0,
            )
        return self._http

    async def close(self):
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    async def get_credential(self, vault_role: str, username: str) -> Tuple[Optional[list], Optional[str]]:
        """Retourne (client_keys, password) pour asyncssh."""
        if SSH_MODE == "vault":
            key, cert = await self._vault_cert(vault_role, username)
            return [(key, cert)], None
        elif SSH_MODE == "vault-kv":
            key = await self._vault_kv_key(vault_role)
            return [key], None
        else:
            return [await self._static_key()], None

    # ── Vault SSH CA mode ─────────────────────────────────────────────────────
    async def _vault_cert(self, role: str, username: str):
        key = asyncssh.generate_private_key("ssh-ed25519")
        pub = key.export_public_key().decode()

        resp = await self._client().post(
            f"/v1/{VAULT_MOUNT}/sign/{role}",
            json={"public_key": pub, "valid_principals": username, "ttl": SSH_TTL},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Vault sign failed ({resp.status_code}): {resp.text}")
        signed = resp.json()["data"]["signed_key"]
        cert = asyncssh.import_certificate(signed)

        log.debug("Vault cert issued: user=%s role=%s ttl=%s", username, role, SSH_TTL)
        return key, cert

    # ── Vault KV mode ─────────────────────────────────────────────────────────
    def _kv_path(self, vault_role: str) -> str:
        return f"{VAULT_KV_PATH}/{vault_role}"

    async def _vault_kv_key(self, vault_role: str):
        cache_key = f"{VAULT_KV_MOUNT}/{self._kv_path(vault_role)}"
        cached = _KV_CACHE.get(cache_key)
        if cached and time.time() - cached[1] < _KV_CACHE_TTL:
            return cached[0]

        resp = await self._client().get(f"/v1/{VAULT_KV_MOUNT}/data/{self._kv_path(vault_role)}")
        if resp.status_code != 200:
            raise RuntimeError(f"Vault KV read failed for role '{vault_role}' ({resp.status_code})")
        data = resp.json()["data"]["data"]
        private_key = data.get("private_key", "")
        if not private_key:
            raise RuntimeError(f"Aucune clé privée dans Vault KV pour le rôle '{vault_role}' — en stocker une via l'UI")
        passphrase = data.get("passphrase") or None

        try:
            key = asyncssh.import_private_key(private_key, passphrase=passphrase)
        except Exception as e:
            raise RuntimeError(f"Clé privée Vault KV illisible (format ou passphrase): {type(e).__name__}")

        _KV_CACHE[cache_key] = (key, time.time())
        return key

    async def store_ssh_key(self, vault_role: str, private_key: str, passphrase: Optional[str] = None) -> None:
        # Valide la clé avant stockage — évite de stocker un credential inutilisable
        try:
            asyncssh.import_private_key(private_key, passphrase=passphrase or None)
        except Exception as e:
            raise RuntimeError(f"Clé privée invalide (format ou passphrase): {type(e).__name__}")
        _KV_CACHE.pop(f"{VAULT_KV_MOUNT}/{self._kv_path(vault_role)}", None)
        data = {"private_key": private_key}
        if passphrase:
            data["passphrase"] = passphrase
        resp = await self._client().post(
            f"/v1/{VAULT_KV_MOUNT}/data/{self._kv_path(vault_role)}",
            json={"data": data},
        )
        if resp.status_code not in (200, 204):
            raise RuntimeError(f"Vault KV write failed ({resp.status_code})")
        log.info("SSH key stored in Vault KV at %s/%s", VAULT_KV_MOUNT, self._kv_path(vault_role))

    async def get_kv_status(self, vault_role: str) -> dict:
        resp = await self._client().get(f"/v1/{VAULT_KV_MOUNT}/data/{self._kv_path(vault_role)}")
        if resp.status_code == 404:
            return {"stored": False, "has_passphrase": False}
        if resp.status_code != 200:
            return {"stored": False, "has_passphrase": False, "error": f"HTTP {resp.status_code}"}
        data = resp.json()["data"]["data"]
        return {
            "stored": bool(data.get("private_key")),
            "has_passphrase": bool(data.get("passphrase")),
        }

    async def delete_ssh_key(self, vault_role: str) -> None:
        _KV_CACHE.pop(f"{VAULT_KV_MOUNT}/{self._kv_path(vault_role)}", None)
        resp = await self._client().delete(f"/v1/{VAULT_KV_MOUNT}/metadata/{self._kv_path(vault_role)}")
        if resp.status_code not in (200, 204):
            raise RuntimeError(f"Vault KV delete failed ({resp.status_code})")
        log.info("SSH key deleted from Vault KV for role '%s'", vault_role)

    # ── Static key mode (dev) ─────────────────────────────────────────────────
    async def _static_key(self) -> str:
        key_path = str(Path(SSH_KEY).expanduser())
        if not Path(key_path).exists():
            raise RuntimeError(
                f"SSH_KEY_PATH={key_path} introuvable. "
                "Générer une clé: ssh-keygen -t ed25519 -f ~/.ssh/mcp_gateway"
            )
        return key_path

    async def verify(self) -> dict:
        if SSH_MODE in ("vault", "vault-kv"):
            try:
                resp = await self._client().get("/v1/auth/token/lookup-self")
                if resp.status_code != 200:
                    return {"mode": SSH_MODE, "ok": False, "error": f"HTTP {resp.status_code}"}
                if SSH_MODE == "vault-kv":
                    return {"mode": "vault-kv", "ok": True, "url": VAULT_URL}
                return {"mode": "vault", "ok": True, "url": VAULT_URL}
            except Exception as e:
                return {"mode": SSH_MODE, "ok": False, "error": str(e)}
        else:
            key_path = str(Path(SSH_KEY).expanduser())
            ok = Path(key_path).exists()
            return {"mode": "key", "ok": ok, "key_path": key_path}
