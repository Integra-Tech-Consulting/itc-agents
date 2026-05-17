# itc-agents

Operating system for the Integra Tech Consulting agent team.

**Replaces** the 166 empty `agent-itc-*` repositories in this org with a single
consolidated monorepo of **10 production agents + 1 orchestrator** running on
top of OpenJarvis.

## Why this repo exists

The previous structure (one GitHub repo per "agent role") was an empty
taxonomy: 166 repositories with a 1-line README and no code, no prompts, no
tools and no handoffs. It is impossible to operate, version, evaluate, or
secure agents that exist only as folder names.

This monorepo replaces that scaffolding with:

- **10 agents** with explicit `goal`, `system_prompt`, `tools`, `handoffs`,
  `guardrails`, `kpis` and `evals` defined in declarative TOML.
- **1 orchestrator** that routes work between them and owns shared state.
- **Shared infrastructure**: Postgres + pgvector memory, central tool
  registry, eval harness, observability hooks.
- **Real integrations** (HubSpot, Stripe, Linear, Gmail, GitHub). No mocks.
  Tools fail loudly if their credential is missing; they never silently
  fabricate results.
- **Reproducible deploy** to the OpenJarvis LXC (CT 200, 192.168.1.178)
  via a single bootstrap script.

## The team

| # | Agent | Goal | Status |
|---|---|---|---|
| 0 | `orchestrator` | Route requests, manage shared state, enforce handoffs | core |
| 1 | `commercial` | Pipeline comercial end-to-end (lead → qualified → won) | core |
| 2 | `delivery_pm` | Gestión de proyectos cliente (kickoff → entrega → cierre) | core |
| 3 | `engineer` | Implementación, infra y on-call asistido | core |
| 4 | `qa_sec` | Tests, OWASP, checklist de release | core |
| 5 | `data_analyst` | Métricas + insight semanal | core |
| 6 | `customer_success` | Onboarding, health score, renovaciones | core |
| 7 | `growth` | Contenido, SEO, campañas | core |
| 8 | `legal_finance` | Contratos, facturación, cobros | core |
| 9 | `governance` | Política, auditoría, AI Act, kill-switch | core |

## Layout

```
itc-agents/
├── agents/                # 10 TOML specs, one per agent
├── shared/
│   ├── memory/schema.sql
│   ├── guardrails/policy.yaml
│   └── tools/             # real integrations (HubSpot, Stripe, ...)
├── evals/                 # harness + datasets per agent
├── deploy/                # register / archive / bootstrap scripts
└── .github/workflows/     # evals on PR, deploy on main
```

## Quick start

```bash
# 1. Local setup
cp .env.example .env             # fill in any keys you have
python -m pip install -r deploy/requirements.txt

# 2. Provision shared infra on the OpenJarvis LXC (one-shot, idempotent)
bash deploy/bootstrap-lxc200.sh

# 3. Register the 10 agents in Jarvis
python deploy/register-with-jarvis.py --jarvis http://127.0.0.1:5055

# 4. Run the eval harness against all 10 agents
python evals/harness.py --all
```

## Safety / kill-switch

- Each agent has a daily $ budget (default $5 in pre-production).
- Any tool that moves money, sends external email, or signs a contract
  requires `require_human_approval` and never executes silently.
- `ITC_AGENTS_DISABLED=true` in the orchestrator's env stops accepting runs.

## Legacy

The 166 `agent-itc-*` repositories in this org are archived (read-only) on
deploy day. Each one's README links back to the new agent that absorbed its
role. See `deploy/role-mapping.json`.
