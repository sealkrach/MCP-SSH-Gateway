"""
CyberArk PAM — provider de credentials SSH
MODE=cyberark-ccp   → Central Credential Provider (REST GET, sans session)
MODE=cyberark-pvwa  → Password Vault Web Access (REST avec session)

Le champ vault_role d'un Host encode la référence CyberArk :
  "SSH_Keys_Safe/server01-root"   → safe="SSH_Keys_Safe", object="server01-root"
  "server01-root"                 → safe=CYBERARK_DEFAULT_SAFE, object="server01-root"
"""
import asyncio, logging, os, tempfile, time
from typing import Optional, Tuple

import httpx

log = logging.getLogger("cyberark")

CYBERARK_URL            = os.getenv("CYBERARK_URL", "")
CYBERARK_MODE           = os.getenv("CYBERARK_MODE", "ccp").lower()
CYBERARK_APP_ID         = os.getenv("CYBERARK_APP_ID", "MCP_SSH_Gateway")
CYBERARK_DEFAULT_SAFE   = os.getenv("CYBERARK_DEFAULT_SAFE", "SSH_Keys_Safe")
CYBERARK_VERIFY_SSL     = os.getenv("CYBERARK_VERIFY_SSL", "true").lower() != "false"
CYBERARK_CA_CERT        = os.getenv("CYBERARK_CA_CERT", "")
CYBERARK_CLIENT_CERT    = os.getenv("CYBERARK_CLIENT_CERT", "")
CYBERARK_CLIENT_KEY     = os.getenv("CYBERARK_CLIENT_KEY", "")
CYBERARK_USERNAME       = os.getenv("CYBERARK_USERNAME", "")
CYBERARK_PASSWORD       = os.getenv("CYBERARK_PASSWORD", "")
CYBERARK_AUTH_METHOD    = os.getenv("CYBERARK_AUTH_METHOD", "ldap")
CYBERARK_SESSION_TIMEOUT = int(os.getenv("CYBERARK_SESSION_TIMEOUT", "1200"))


def _parse_role(vault_role: str) -> Tuple[str, str]:
    """Découpe "Safe/Object" → (safe, object). Sans '/', utilise le safe par défaut."""
    if "/" in vault_role:
        safe, obj = vault_role.split("/", 1)
        return safe.strip(), obj.strip()
    return CYBERARK_DEFAULT_SAFE, vault_role.strip()


def _build_ssl() -> httpx.AsyncClient:
    """Construit le client httpx avec les options TLS correctes."""
    ssl_context: bool | str = CYBERARK_VERIFY_SSL
    if CYBERARK_CA_CERT:
        ssl_context = CYBERARK_CA_CERT

    cert = None
    if CYBERARK_CLIENT_CERT and CYBERARK_CLIENT_KEY:
        cert = (CYBERARK_CLIENT_CERT, CYBERARK_CLIENT_KEY)

    return httpx.AsyncClient(verify=ssl_context, cert=cert, timeout=15.0)


class CyberArkSSHClient:
    """Provider de credentials SSH via CyberArk PAM."""

    def __init__(self) -> None:
        self._pvwa_token: Optional[str] = None
        self._pvwa_expires_at: float = 0.0

    # ── Interface publique ────────────────────────────────────────────────────

    async def get_credential(
        self, vault_role: str, username: str
    ) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        """Retourne (key_path, cert_path, passphrase, password).
        key_path non-None → auth par clé ; password non-None → auth par mot de passe."""
        safe, obj = _parse_role(vault_role)
        data = await self._fetch(safe, obj)
        return self._parse_credential(data)

    async def verify(self) -> dict:
        """Teste la connectivité — même shape que VaultSSHClient.verify()."""
        if not CYBERARK_URL:
            return {"mode": f"cyberark-{CYBERARK_MODE}", "ok": False,
                    "error": "CYBERARK_URL non configuré"}
        mode_label = f"cyberark-{CYBERARK_MODE}"
        try:
            if CYBERARK_MODE == "ccp":
                # Ping CCP avec un objet fictif — on attend 404 ou 200, pas une erreur réseau
                async with _build_ssl() as client:
                    r = await client.get(
                        f"{CYBERARK_URL}/AIMWebService/api/Accounts",
                        params={"AppID": CYBERARK_APP_ID, "Safe": "ping", "Object": "ping"},
                    )
                ok = r.status_code in (200, 404, 400, 403)
            else:
                # PVWA : tente un login pour valider les credentials
                await self._pvwa_login()
                ok = self._pvwa_token is not None
            return {"mode": mode_label, "ok": ok, "url": CYBERARK_URL}
        except Exception as e:
            return {"mode": mode_label, "ok": False, "url": CYBERARK_URL, "error": str(e)}

    # ── Dispatch ─────────────────────────────────────────────────────────────

    async def _fetch(self, safe: str, obj: str) -> dict:
        if not CYBERARK_URL:
            raise RuntimeError("CYBERARK_URL n'est pas configuré")
        if CYBERARK_MODE == "ccp":
            return await self._fetch_ccp(safe, obj)
        return await self._fetch_pvwa(safe, obj)

    # ── CCP ──────────────────────────────────────────────────────────────────

    async def _fetch_ccp(self, safe: str, obj: str) -> dict:
        """GET /AIMWebService/api/Accounts?AppID=...&Safe=...&Object=..."""
        url = f"{CYBERARK_URL}/AIMWebService/api/Accounts"
        params = {
            "AppID": CYBERARK_APP_ID,
            "Safe":  safe,
            "Object": obj,
            "Reason": "MCP_SSH_Gateway",
        }
        async with _build_ssl() as client:
            r = await client.get(url, params=params)
        if r.status_code != 200:
            raise RuntimeError(
                f"CyberArk CCP {r.status_code} pour Safe={safe} Object={obj}: {r.text[:200]}"
            )
        data = r.json()
        log.debug("CCP credential retrieved: Safe=%s Object=%s", safe, obj)
        return data

    # ── PVWA ─────────────────────────────────────────────────────────────────

    async def _fetch_pvwa(self, safe: str, obj: str) -> dict:
        """Login → search account → retrieve secret."""
        await self._pvwa_ensure_token()

        async with _build_ssl() as client:
            # Recherche du compte dans le Safe
            r = await client.get(
                f"{CYBERARK_URL}/PasswordVault/API/Accounts",
                params={"search": obj, "SafeName": safe},
                headers={"Authorization": self._pvwa_token},
            )
            if r.status_code != 200:
                raise RuntimeError(
                    f"CyberArk PVWA search {r.status_code} Safe={safe} Object={obj}: {r.text[:200]}"
                )
            accounts = r.json().get("value", [])
            if not accounts:
                raise RuntimeError(f"Aucun compte CyberArk trouvé: Safe={safe} Object={obj}")

            account_id = accounts[0]["id"]
            log.debug("PVWA account found: id=%s Safe=%s Object=%s", account_id, safe, obj)

            # Récupération du secret
            r2 = await client.post(
                f"{CYBERARK_URL}/PasswordVault/API/Accounts/{account_id}/Password/Retrieve",
                json={"reason": "MCP_SSH_Gateway"},
                headers={"Authorization": self._pvwa_token},
            )
            if r2.status_code != 200:
                raise RuntimeError(
                    f"CyberArk PVWA retrieve {r2.status_code} account={account_id}: {r2.text[:200]}"
                )

        # Le contenu du secret est renvoyé comme string JSON brute
        content = r2.json() if isinstance(r2.json(), str) else r2.text.strip('"')
        return {
            "Content": content,
            "UserName": accounts[0].get("userName", ""),
            "Address":  accounts[0].get("address", ""),
            "Name":     accounts[0].get("name", ""),
        }

    async def _pvwa_ensure_token(self) -> None:
        """Renouvelle le token PVWA si expiré ou absent."""
        if self._pvwa_token and time.time() < self._pvwa_expires_at - 60:
            return
        await self._pvwa_login()

    async def _pvwa_login(self) -> None:
        if not CYBERARK_USERNAME or not CYBERARK_PASSWORD:
            raise RuntimeError(
                "CYBERARK_USERNAME et CYBERARK_PASSWORD requis pour le mode pvwa"
            )
        url = f"{CYBERARK_URL}/PasswordVault/API/auth/{CYBERARK_AUTH_METHOD}/Logon"
        async with _build_ssl() as client:
            r = await client.post(url, json={
                "username": CYBERARK_USERNAME,
                "password": CYBERARK_PASSWORD,
                "concurrentSession": True,
            })
        if r.status_code != 200:
            raise RuntimeError(
                f"CyberArk PVWA login {r.status_code}: {r.text[:200]}"
            )
        # Le token est renvoyé comme string JSON brute (avec guillemets)
        token = r.json() if isinstance(r.json(), str) else r.text.strip('"')
        self._pvwa_token = token
        self._pvwa_expires_at = time.time() + CYBERARK_SESSION_TIMEOUT
        log.debug("PVWA session renewed, expires in %ds", CYBERARK_SESSION_TIMEOUT)

    # ── Parsing du credential ─────────────────────────────────────────────────

    def _parse_credential(
        self, data: dict
    ) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        """
        Détecte le type de credential retourné par CyberArk :
        - PEM (-----BEGIN ...) → clé privée SSH, écrite dans un fichier temp
        - Autre               → mot de passe SSH
        """
        content = data.get("Content", "")
        if not content:
            raise RuntimeError("CyberArk a renvoyé un credential vide")

        if content.strip().startswith("-----BEGIN"):
            kf = tempfile.NamedTemporaryFile(delete=False, suffix=".key", mode="w")
            kf.write(content)
            kf.close()
            os.chmod(kf.name, 0o600)
            passphrase = data.get("passphrase") or None
            log.debug("CyberArk: credential type=ssh-key user=%s", data.get("UserName", ""))
            return kf.name, None, passphrase, None
        else:
            log.debug("CyberArk: credential type=password user=%s", data.get("UserName", ""))
            return None, None, None, content
