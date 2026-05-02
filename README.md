# MCP SSH Gateway

A self-hosted SSH gateway that lets Claude execute commands on your servers through the Model Context Protocol (MCP). Private keys are stored in HashiCorp Vault. Every command is audited, rate-limited, and filtered by OPA-compatible policies.

## Features

- **MCP integration** — Claude can list hosts, run commands, and read audit logs via `ssh_exec`, `ssh_list_hosts`, `ssh_audit`
- **Vault KV** — SSH private key and passphrase stored encrypted in Vault, retrieved at connection time
- **Vault SSH CA** — Alternatively, generate ephemeral ED25519 certificates (TTL 5 min) signed by Vault
- **Policy engine** — Regex-based rules (block `rm -rf`, `sudo`, interactive shells, etc.) applied before every exec
- **Audit log** — Immutable per-command log with SHA-256 stdout hash, duration, exit code, deny reason
- **RBAC** — JWT-based roles (`admin`, `ops`, `sre`, `dev`) scoped per host
- **Rate limiting** — Per-user, per-host, per-environment (separate limits for prod / staging / dev)
- **React UI** — Dashboard, host registry, policy editor, audit viewer, SSH credential management
- **Observability** — Prometheus metrics, pre-wired Grafana stack

## Architecture

```
Claude Code
    │  MCP stdio (server.py)
    ▼
mcp_server ──HTTP──► gateway (FastAPI)
                         │
                 ┌───────┼───────┐
                 │       │       │
              Vault   Postgres  asyncssh
              (KV +   (hosts,   (SSH exec)
              SSH CA)  audit)
```

## Quick start

```bash
git clone https://github.com/sealkrach/MCP-SSH-Gateway
cd MCP-SSH-Gateway

cp .env.example .env
# Edit .env — set JWT_SECRET (32+ chars) and DB_PASSWORD at minimum

docker compose up --build -d
```

| Service    | URL                    |
|------------|------------------------|
| UI         | http://localhost:3000  |
| API        | http://localhost:8080  |
| Vault      | http://localhost:8200  |
| Prometheus | http://localhost:9090  |
| Grafana    | http://localhost:3001  |

Default login: `admin / admin123`

## SSH modes

### `vault-kv` (recommended)

Store your private key in Vault via the UI (Settings → SSH / Vault → Vault KV):

1. Paste your PEM private key
2. Enter passphrase (optional)
3. Click **Stocker dans Vault**
4. Set `SSH_MODE=vault-kv` in `.env` and restart:

```bash
docker compose up -d gateway
```

### `vault` — Vault SSH CA (production)

Vault signs ephemeral ED25519 certificates per connection. Requires a configured Vault SSH secrets engine:

```bash
vault secrets enable -path=ssh ssh
vault write ssh/roles/dev-ssh \
  key_type=ca allow_user_certificates=true \
  default_user=deploy ttl=5m
```

Then set `SSH_MODE=vault` and configure `VAULT_URL`, `VAULT_TOKEN`, `VAULT_MOUNT` in `.env`.

### `key` — static key (dev only)

Mounts `~/.ssh/id_rsa` from the host. Set `SSH_MODE=key` (default).

## Connecting Claude Code

```bash
claude mcp add ssh-gateway \
  -e MCP_SSH_GATEWAY_URL=http://localhost:8080 \
  -e MCP_SSH_USER=admin \
  -e MCP_SSH_PASS=admin123 \
  -- python3 /path/to/MCP-SSH-Gateway/mcp_server/server.py
```

Verify the connection:

```bash
claude mcp list
# ssh-gateway: python3 ... - ✓ Connected
```

Claude can then:

```
List my SSH hosts
Run "df -h" on prod-web-01
Show me the last 20 audit entries
```

## Available MCP tools

| Tool | Description |
|------|-------------|
| `ssh_exec(host, command, timeout?)` | Execute a command on a registered host |
| `ssh_list_hosts(env?)` | List accessible hosts, optionally filtered by environment |
| `ssh_audit(limit?, env?, status?)` | Query the audit log |

## API reference

```
POST /auth/token          Login → JWT
GET  /auth/me             Current user

GET  /hosts               List hosts
POST /hosts               Create host (admin)
PUT  /hosts/{id}          Update host (admin)
DELETE /hosts/{id}        Disable host (admin)

POST /exec                Execute SSH command

GET  /policies            List policies
POST /policies            Create policy (admin)
PUT  /policies/{id}       Update policy (admin)
DELETE /policies/{id}     Delete policy (admin)

POST /vault/credentials           Store SSH key in Vault (admin)
GET  /vault/credentials/status    Check if key is stored
DELETE /vault/credentials         Remove key from Vault (admin)

GET  /audit               Audit log (filterable)
GET  /metrics/summary     Dashboard metrics
GET  /metrics/prometheus  Prometheus scrape endpoint
GET  /health              Health + Vault status
```

## Configuration

All settings via `.env` (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `JWT_SECRET` | — | Secret key for JWT signing (32+ chars, **required**) |
| `JWT_EXPIRE_HOURS` | `24` | Token lifetime |
| `DB_PASSWORD` | — | PostgreSQL password (**required**) |
| `SSH_MODE` | `key` | `key` / `vault-kv` / `vault` |
| `VAULT_URL` | `http://vault:8200` | Vault address |
| `VAULT_TOKEN` | `dev-only-token` | Vault root/service token |
| `VAULT_KV_MOUNT` | `secret` | Vault KV v2 mount |
| `VAULT_KV_PATH` | `mcp-gateway/ssh-key` | Path for stored SSH key |
| `RATE_PROD` | `10` | Max commands/min in production |
| `RATE_STG` | `30` | Max commands/min in staging |
| `RATE_DEV` | `100` | Max commands/min in development |
| `GATEWAY_PORT` | `8080` | Gateway API port |
| `UI_PORT` | `3000` | UI port |

## Default users

| Username | Password | Roles |
|----------|----------|-------|
| `admin` | `admin123` | admin, ops, sre, dev |
| `ops` | `ops123` | ops, sre |
| `dev` | `dev123` | dev |

Customize by creating a `users.json` file or setting the `GATEWAY_USERS` env var (JSON array).

## Default policies

Out-of-the-box rules that block dangerous commands:

- `rm -rf` — critical, all envs
- `sudo` — high, all envs
- Interactive shells (`bash`, `sh`, `zsh`) — high, production
- Direct DB access (`psql`, `mysql`, `mongosh`) — critical, production
- `curl … | bash` — critical, all envs
- `chmod 777` — high, all envs

All rules are editable in the UI (Policies tab) or via the API.

## Kubernetes

```bash
docker build -t registry.example.com/mcp-ssh-gateway:2.0 .
docker push registry.example.com/mcp-ssh-gateway:2.0

helm upgrade --install mcp-ssh ./helm \
  --set image.repository=registry.example.com/mcp-ssh-gateway \
  --set env.JWT_SECRET=your-32-char-secret \
  --set database.password=your-db-password \
  --set ingress.host=mcp-ssh.example.internal
```

## Security notes

- **Never commit `.env`** — it is in `.gitignore`. Use secrets management in production.
- Change default passwords before any internet-facing deployment.
- The dev-mode Vault token (`dev-only-token`) is public — replace with a real token in production.
- `SSH_MODE=key` mounts your host SSH key into the container — use `vault-kv` or `vault` in production.
