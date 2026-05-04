#!/bin/sh
# Vault persistent startup: init (first run only) → unseal → create stable token

export VAULT_ADDR="http://127.0.0.1:8200"
INIT_FILE="/vault/data/.init"
STABLE_TOKEN="${VAULT_STABLE_TOKEN:-dev-only-token}"

# Start Vault server in background
vault server -config=/vault/config/config.hcl &

# Wait for API to respond (sealed or not — just needs to be up)
echo "[vault] Démarrage..."
until vault status 2>&1 | grep -q "Seal Type"; do
    sleep 1
done

# First run: initialize with a single unseal key
if vault status 2>&1 | grep -q "Initialized.*false"; then
    echo "[vault] Initialisation (premier démarrage)..."
    vault operator init -key-shares=1 -key-threshold=1 > "$INIT_FILE"
    chmod 600 "$INIT_FILE"
fi

# Unseal — required after every restart with file backend
if vault status 2>&1 | grep -q "Sealed.*true"; then
    KEY=$(awk '/Unseal Key 1:/ {print $NF}' "$INIT_FILE")
    vault operator unseal "$KEY" > /dev/null
    echo "[vault] Descellé"
fi

# Authenticate with the generated root token
ROOT_TOKEN=$(awk '/Initial Root Token:/ {print $NF}' "$INIT_FILE")
export VAULT_TOKEN="$ROOT_TOKEN"

# Create a stable token with the known ID from .env (idempotent)
vault token lookup "$STABLE_TOKEN" > /dev/null 2>&1 || \
    vault token create -id="$STABLE_TOKEN" -policy=root -orphan > /dev/null 2>&1

# Enable secrets engines (idempotent — error if already enabled is suppressed)
vault secrets enable -path=secret kv-v2 2>/dev/null || true
vault secrets enable -path=ssh    ssh    2>/dev/null || true

echo "[vault] Prêt — token: $STABLE_TOKEN"
wait
