"""
Vault SSH Secrets Engine + Vault KV + fallback clé statique pour dev
MODE=key       → clé privée statique (dev/test)
MODE=vault     → certificats éphémères signés par Vault SSH CA (prod)
MODE=vault-kv  → clé privée stockée dans Vault KV (clé uploadée via UI)
"""
import asyncio, logging, os, tempfile
from pathlib import Path
from typing import Optional, Tuple

log = logging.getLogger("vault-ssh")

VAULT_URL   = os.getenv("VAULT_URL", "http://localhost:8200")
VAULT_TOKEN = os.getenv("VAULT_TOKEN", "")
VAULT_MOUNT = os.getenv("VAULT_MOUNT", "ssh")
VAULT_KV_MOUNT = os.getenv("VAULT_KV_MOUNT", "secret")
VAULT_KV_PATH  = os.getenv("VAULT_KV_PATH", "mcp-gateway/ssh-key")
SSH_MODE    = os.getenv("SSH_MODE", "key")
SSH_KEY     = os.getenv("SSH_KEY_PATH", "~/.ssh/id_rsa")
SSH_TTL     = os.getenv("VAULT_SSH_TTL", "5m")


class VaultSSHClient:

    async def get_credential(self, vault_role: str, username: str) -> Tuple[str, Optional[str], Optional[str]]:
        """Retourne (key_path, cert_path|None, passphrase|None)"""
        if SSH_MODE == "vault":
            key_path, cert_path = await self._vault_cert(vault_role, username)
            return key_path, cert_path, None
        elif SSH_MODE == "vault-kv":
            key_path, passphrase = await self._vault_kv_key()
            return key_path, None, passphrase
        else:
            key_path = await self._static_key()
            return key_path, None, None

    # ── Vault SSH CA mode ─────────────────────────────────────────────────────
    async def _vault_cert(self, role: str, username: str) -> Tuple[str, str]:
        return await asyncio.get_event_loop().run_in_executor(
            None, self._vault_cert_sync, role, username
        )

    def _vault_cert_sync(self, role: str, username: str) -> Tuple[str, str]:
        import hvac, asyncssh
        client = hvac.Client(url=VAULT_URL, token=VAULT_TOKEN)
        if not client.is_authenticated():
            raise RuntimeError("Vault auth failed — vérifier VAULT_TOKEN")

        key = asyncssh.generate_private_key("ssh-ed25519")
        pub = key.export_public_key().decode()

        resp = client.secrets.ssh.sign_ssh_key(
            name=role, public_key=pub,
            valid_principals=username, ttl=SSH_TTL,
            mount_point=VAULT_MOUNT,
        )
        signed = resp["data"]["signed_key"]

        kf = tempfile.NamedTemporaryFile(delete=False, suffix=".key", mode="wb")
        kf.write(key.export_private_key()); kf.close()
        os.chmod(kf.name, 0o600)

        cf = tempfile.NamedTemporaryFile(delete=False, suffix="-cert.pub", mode="w")
        cf.write(signed); cf.close()

        log.debug("Vault cert issued: user=%s role=%s ttl=%s", username, role, SSH_TTL)
        return kf.name, cf.name

    # ── Vault KV mode ─────────────────────────────────────────────────────────
    async def _vault_kv_key(self) -> Tuple[str, Optional[str]]:
        return await asyncio.get_event_loop().run_in_executor(
            None, self._vault_kv_key_sync
        )

    def _vault_kv_key_sync(self) -> Tuple[str, Optional[str]]:
        import hvac
        client = hvac.Client(url=VAULT_URL, token=VAULT_TOKEN)
        if not client.is_authenticated():
            raise RuntimeError("Vault auth failed — vérifier VAULT_TOKEN")

        secret = client.secrets.kv.v2.read_secret_version(
            path=VAULT_KV_PATH, mount_point=VAULT_KV_MOUNT
        )
        data = secret["data"]["data"]
        private_key = data.get("private_key", "")
        if not private_key:
            raise RuntimeError("Aucune clé privée dans Vault KV — en stocker une via l'UI")
        passphrase = data.get("passphrase") or None

        kf = tempfile.NamedTemporaryFile(delete=False, suffix=".key", mode="w")
        kf.write(private_key); kf.close()
        os.chmod(kf.name, 0o600)
        return kf.name, passphrase

    async def store_ssh_key(self, private_key: str, passphrase: Optional[str] = None) -> None:
        await asyncio.get_event_loop().run_in_executor(
            None, self._store_ssh_key_sync, private_key, passphrase
        )

    def _store_ssh_key_sync(self, private_key: str, passphrase: Optional[str]) -> None:
        import hvac
        client = hvac.Client(url=VAULT_URL, token=VAULT_TOKEN)
        if not client.is_authenticated():
            raise RuntimeError("Vault auth failed — vérifier VAULT_TOKEN")
        data = {"private_key": private_key}
        if passphrase:
            data["passphrase"] = passphrase
        client.secrets.kv.v2.create_or_update_secret(
            path=VAULT_KV_PATH, secret=data, mount_point=VAULT_KV_MOUNT
        )
        log.info("SSH key stored in Vault KV at %s/%s", VAULT_KV_MOUNT, VAULT_KV_PATH)

    async def get_kv_status(self) -> dict:
        return await asyncio.get_event_loop().run_in_executor(
            None, self._get_kv_status_sync
        )

    def _get_kv_status_sync(self) -> dict:
        import hvac
        client = hvac.Client(url=VAULT_URL, token=VAULT_TOKEN)
        if not client.is_authenticated():
            return {"stored": False, "has_passphrase": False, "error": "Vault auth failed"}
        try:
            secret = client.secrets.kv.v2.read_secret_version(
                path=VAULT_KV_PATH, mount_point=VAULT_KV_MOUNT
            )
            data = secret["data"]["data"]
            return {
                "stored": bool(data.get("private_key")),
                "has_passphrase": bool(data.get("passphrase")),
            }
        except Exception:
            return {"stored": False, "has_passphrase": False}

    async def delete_ssh_key(self) -> None:
        await asyncio.get_event_loop().run_in_executor(
            None, self._delete_ssh_key_sync
        )

    def _delete_ssh_key_sync(self) -> None:
        import hvac
        client = hvac.Client(url=VAULT_URL, token=VAULT_TOKEN)
        if not client.is_authenticated():
            raise RuntimeError("Vault auth failed — vérifier VAULT_TOKEN")
        client.secrets.kv.v2.delete_metadata_and_all_versions(
            path=VAULT_KV_PATH, mount_point=VAULT_KV_MOUNT
        )
        log.info("SSH key deleted from Vault KV")

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
        if SSH_MODE == "vault":
            try:
                import hvac
                c = hvac.Client(url=VAULT_URL, token=VAULT_TOKEN)
                ok = c.is_authenticated()
                return {"mode": "vault", "ok": ok, "url": VAULT_URL}
            except Exception as e:
                return {"mode": "vault", "ok": False, "error": str(e)}
        elif SSH_MODE == "vault-kv":
            try:
                import hvac
                c = hvac.Client(url=VAULT_URL, token=VAULT_TOKEN)
                if not c.is_authenticated():
                    return {"mode": "vault-kv", "ok": False, "error": "Vault auth failed"}
                status = await self.get_kv_status()
                return {"mode": "vault-kv", "ok": status.get("stored", False),
                        "url": VAULT_URL, "key_stored": status.get("stored", False),
                        "has_passphrase": status.get("has_passphrase", False)}
            except Exception as e:
                return {"mode": "vault-kv", "ok": False, "error": str(e)}
        else:
            key_path = str(Path(SSH_KEY).expanduser())
            ok = Path(key_path).exists()
            return {"mode": "key", "ok": ok, "key_path": key_path}
