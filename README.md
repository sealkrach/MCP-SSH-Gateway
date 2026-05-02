# MCP SSH Gateway v2 — Enterprise

## 🚀 Démarrage rapide (Docker Compose)

```bash
cp .env.example .env
# Éditer .env : JWT_SECRET, DB_PASSWORD au minimum

docker compose up --build -d

# UI   → http://localhost:3000   (admin/admin123)
# API  → http://localhost:8080
# Prom → http://localhost:9090
# Graf → http://localhost:3001
```

## 🔧 Intégration Claude Code

Copier `claude_desktop_config.example.json` dans ton `~/Library/Application Support/Claude/claude_desktop_config.json`
et adapter le chemin absolu + redémarrer Claude Desktop.

## ☸️ Kubernetes (Helm)

```bash
# Build & push image
docker build -t registry.internal/mcp-ssh-gateway:2.0 .
docker push registry.internal/mcp-ssh-gateway:2.0

# Deploy
helm upgrade --install mcp-ssh ./helm \
  --set image.repository=registry.internal/mcp-ssh-gateway \
  --set env.JWT_SECRET=VOTRE_SECRET_32CHARS \
  --set database.password=VOTRE_DB_PASS \
  --set ingress.host=mcp-ssh.votredomaine.internal
```

## 🔑 Utilisateurs par défaut

| User    | Password     | Rôles                     |
|---------|-------------|---------------------------|
| admin   | admin123    | admin, ops, sre, dev      |
| sylvain | sylvain123  | admin, ops, sre           |
| ops     | ops123      | ops, sre                  |
| dev     | dev123      | dev                       |

Personnaliser dans `users.json` ou ENV `GATEWAY_USERS`.

## 📋 Outils MCP disponibles

- `ssh_exec(host, command)` — Exécuter une commande
- `ssh_list_hosts()` — Lister les hôtes accessibles
- `ssh_audit()` — Consulter les logs
- `ssh_health()` — État du gateway
