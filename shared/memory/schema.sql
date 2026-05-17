-- ITC agents shared memory schema (Postgres 16 + pgvector)
-- Apply with: psql "$DATABASE_URL" -f shared/memory/schema.sql

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ─── Agent runs (audit trail; every invocation logged) ──────────────
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id        TEXT NOT NULL,
    agent_name      TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at        TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'running',  -- running|ok|error|aborted
    input_json      JSONB NOT NULL,
    output_json     JSONB,
    cost_usd        NUMERIC(10,4) DEFAULT 0,
    input_tokens    INTEGER DEFAULT 0,
    output_tokens   INTEGER DEFAULT 0,
    latency_ms      INTEGER,
    error           TEXT,
    parent_run_id   UUID REFERENCES agent_runs(run_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS agent_runs_agent_idx ON agent_runs(agent_name, started_at DESC);
CREATE INDEX IF NOT EXISTS agent_runs_status_idx ON agent_runs(status);

-- ─── Tool calls (every tool invocation, every policy decision) ──────
CREATE TABLE IF NOT EXISTS tool_calls (
    call_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    agent_name      TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    payload_json    JSONB,
    policy_decision TEXT,   -- approved|denied|needs_human_approval
    policy_reason   TEXT,
    result_json     JSONB,
    error           TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    duration_ms     INTEGER
);
CREATE INDEX IF NOT EXISTS tool_calls_agent_idx ON tool_calls(agent_name, started_at DESC);
CREATE INDEX IF NOT EXISTS tool_calls_policy_idx ON tool_calls(policy_decision);

-- ─── Handoffs between agents ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS handoffs (
    handoff_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_agent      TEXT NOT NULL,
    to_agent        TEXT NOT NULL,
    payload_json    JSONB NOT NULL,
    rationale       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    parent_run_id   UUID REFERENCES agent_runs(run_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS handoffs_to_idx ON handoffs(to_agent, created_at DESC);

-- ─── Long-term memory (semantic, namespaced per agent) ──────────────
CREATE TABLE IF NOT EXISTS agent_memory (
    memory_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    namespace       TEXT NOT NULL,        -- e.g. 'commercial', 'governance/audits'
    key             TEXT,
    content         TEXT NOT NULL,
    embedding       vector(1536),
    metadata_json   JSONB DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ            -- PII auto-expiry support
);
CREATE INDEX IF NOT EXISTS agent_memory_ns_idx ON agent_memory(namespace);
CREATE INDEX IF NOT EXISTS agent_memory_expires_idx ON agent_memory(expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS agent_memory_embedding_idx
    ON agent_memory USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- ─── Daily budget tracking ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS daily_budgets (
    day             DATE NOT NULL,
    agent_name      TEXT NOT NULL,
    spent_usd       NUMERIC(10,4) NOT NULL DEFAULT 0,
    PRIMARY KEY (day, agent_name)
);

-- ─── Eval runs (eval harness writes here) ───────────────────────────
CREATE TABLE IF NOT EXISTS eval_runs (
    eval_run_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_name      TEXT NOT NULL,
    dataset         TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    cases_total     INTEGER NOT NULL,
    cases_passed    INTEGER NOT NULL,
    pass_rate       NUMERIC(5,4) NOT NULL,
    threshold       NUMERIC(5,4) NOT NULL,
    passed          BOOLEAN NOT NULL,
    report_md       TEXT
);
CREATE INDEX IF NOT EXISTS eval_runs_agent_idx ON eval_runs(agent_name, started_at DESC);

-- ─── Convenience views ──────────────────────────────────────────────
CREATE OR REPLACE VIEW v_daily_spend AS
    SELECT day, agent_name, spent_usd
    FROM daily_budgets
    ORDER BY day DESC, agent_name;

CREATE OR REPLACE VIEW v_policy_violations AS
    SELECT agent_name, tool_name, policy_decision, policy_reason, started_at
    FROM tool_calls
    WHERE policy_decision IN ('denied', 'needs_human_approval')
    ORDER BY started_at DESC;
