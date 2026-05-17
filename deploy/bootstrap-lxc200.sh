#!/usr/bin/env bash
# Bootstrap shared infra for itc-agents on the OpenJarvis LXC (CT 200).
# Idempotent: safe to run multiple times.
#
# Expected to run AS ROOT inside the LXC, e.g.:
#   ssh root@192.168.1.9 "pct push 200 deploy/bootstrap-lxc200.sh /root/ && pct exec 200 -- bash /root/bootstrap-lxc200.sh"
#
# Or directly via:
#   pct enter 200 ; bash /root/itc-agents/deploy/bootstrap-lxc200.sh

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Integra-Tech-Consulting/itc-agents.git}"
REPO_DIR="${REPO_DIR:-/opt/itc-agents}"
PG_DB="${PG_DB:-itc_agents}"
PG_USER="${PG_USER:-itc_agents}"
PG_PASS_FILE="/root/.itc-agents/db-password"

log() { printf '[bootstrap] %s\n' "$*"; }

# ─── 1. APT deps ─────────────────────────────────────────────────────
log "installing apt deps"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends \
    git curl ca-certificates python3 python3-venv python3-pip \
    postgresql-16 postgresql-16-pgvector >/dev/null

# ─── 2. Repo ─────────────────────────────────────────────────────────
if [[ ! -d "$REPO_DIR/.git" ]]; then
    log "cloning $REPO_URL -> $REPO_DIR"
    git clone --depth=1 "$REPO_URL" "$REPO_DIR"
else
    log "repo present, pulling latest"
    (cd "$REPO_DIR" && git pull --ff-only) || log "git pull skipped (offline or dirty)"
fi

# ─── 3. Python venv + deps ───────────────────────────────────────────
log "installing Python deps"
python3 -m venv "$REPO_DIR/.venv"
"$REPO_DIR/.venv/bin/pip" install -q --upgrade pip
"$REPO_DIR/.venv/bin/pip" install -q -r "$REPO_DIR/deploy/requirements.txt"

# ─── 4. Postgres + pgvector ──────────────────────────────────────────
log "configuring Postgres"
systemctl enable --now postgresql

mkdir -p /root/.itc-agents
chmod 700 /root/.itc-agents
if [[ ! -s "$PG_PASS_FILE" ]]; then
    head -c 24 /dev/urandom | base64 | tr -d '/+=\n' > "$PG_PASS_FILE"
    chmod 600 "$PG_PASS_FILE"
fi
PG_PASS="$(cat "$PG_PASS_FILE")"

sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$PG_USER'" \
  | grep -q 1 || sudo -u postgres psql -c \
    "CREATE ROLE $PG_USER LOGIN PASSWORD '$PG_PASS'"
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$PG_DB'" \
  | grep -q 1 || sudo -u postgres createdb -O "$PG_USER" "$PG_DB"

log "applying schema"
sudo -u postgres psql -d "$PG_DB" -v ON_ERROR_STOP=1 -f "$REPO_DIR/shared/memory/schema.sql" >/dev/null

# ─── 5. secrets.env (placeholder; user fills it) ─────────────────────
SECRETS=/root/.itc-agents/secrets.env
if [[ ! -f "$SECRETS" ]]; then
    log "writing secrets.env template (FILL IT IN)"
    cat > "$SECRETS" <<EOF
DATABASE_URL=postgresql://${PG_USER}:${PG_PASS}@127.0.0.1:5432/${PG_DB}
JARVIS_URL=http://127.0.0.1:5055
# Real-API credentials (fill or the corresponding agent tools will fail loudly)
HUBSPOT_API_KEY=
STRIPE_API_KEY=
STRIPE_API_KEY_MODE=test
LINEAR_API_KEY=
GMAIL_OAUTH_CLIENT_ID=
GMAIL_OAUTH_CLIENT_SECRET=
GMAIL_OAUTH_REFRESH_TOKEN=
GITHUB_BOT_TOKEN=
ITC_AGENTS_DISABLED=false
ITC_REQUIRE_HUMAN_APPROVAL=true
EOF
    chmod 600 "$SECRETS"
fi

# ─── 6. systemd unit for the registration helper (oneshot) ───────────
cat > /etc/systemd/system/itc-agents-register.service <<EOF
[Unit]
Description=Register ITC agents in OpenJarvis (oneshot, idempotent)
After=network-online.target jarvis-backend.service
Wants=jarvis-backend.service

[Service]
Type=oneshot
EnvironmentFile=/root/.itc-agents/secrets.env
ExecStart=$REPO_DIR/.venv/bin/python $REPO_DIR/deploy/register-with-jarvis.py --jarvis \${JARVIS_URL}
User=root
WorkingDirectory=$REPO_DIR

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable itc-agents-register.service >/dev/null

log "bootstrap complete."
log "Next steps:"
log "  1. Fill /root/.itc-agents/secrets.env with the credentials you have."
log "  2. systemctl start itc-agents-register.service"
log "  3. curl http://127.0.0.1:5055/v1/managed-agents | jq '.agents[].name'"
