"""Persistent memory tools for ITC agents.

Backed by the `agent_memory` table (pgvector) defined in shared/memory/schema.sql.
Two callables are exposed:

- ``store(agent_name, namespace, content, key=None, metadata=None, ttl_days=None)``
- ``retrieve(agent_name, namespace, query, k=8)``

Embedding strategy
------------------
If ``OPENAI_API_KEY`` is set, content/query are embedded with
``text-embedding-3-small`` (1536 dims, matches schema). Vector similarity is
used for retrieval.

If no embedding credential is present, we degrade gracefully:
content is stored with ``embedding = NULL`` and retrieval falls back to
case-insensitive substring match on ``content`` plus recency ordering. This is
intentional — Jarvis complained that agents had **no** memory; partial
semantic memory is strictly better than none and works without external
dependencies.

Errors raise :class:`shared.tools._base.ToolError` subclasses so the agent
sees a real failure rather than a mock.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional

from ._base import ToolCredentialError, ToolError, ToolUpstreamError, http_json

try:  # psycopg3 preferred (matches deploy requirements)
    import psycopg
    from psycopg.rows import dict_row
    _PSYCOPG = "psycopg3"
except ImportError:  # pragma: no cover - fallback for older envs
    try:
        import psycopg2 as psycopg  # type: ignore[no-redef]
        from psycopg2.extras import RealDictCursor
        dict_row = RealDictCursor  # type: ignore[assignment]
        _PSYCOPG = "psycopg2"
    except ImportError:
        psycopg = None  # type: ignore[assignment]
        dict_row = None  # type: ignore[assignment]
        _PSYCOPG = None


EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536


def _conn():
    if psycopg is None:
        raise ToolError(
            "psycopg is not installed. Add 'psycopg[binary]' to deploy/requirements.txt "
            "and rerun the bootstrap script."
        )
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise ToolCredentialError(
            "DATABASE_URL is not set. Memory tools require Postgres+pgvector."
        )
    if _PSYCOPG == "psycopg3":
        return psycopg.connect(dsn, row_factory=dict_row, autocommit=True)
    return psycopg.connect(dsn)  # psycopg2


def _embed(text: str) -> Optional[List[float]]:
    """Return embedding for ``text`` or None if no embedding provider is configured."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        return None
    if not text:
        return None
    # Truncate to a sane size (OpenAI accepts ~8k tokens; ~32k chars is safe).
    snippet = text[:32000]
    resp = http_json(
        "POST",
        "https://api.openai.com/v1/embeddings",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json={"model": EMBED_MODEL, "input": snippet},
        timeout=30.0,
    )
    try:
        vec = resp["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ToolUpstreamError(f"OpenAI embeddings: unexpected response shape: {exc}")
    if len(vec) != EMBED_DIM:
        raise ToolUpstreamError(
            f"OpenAI embeddings returned dim={len(vec)}, expected {EMBED_DIM}"
        )
    return vec


def _vector_literal(vec: List[float]) -> str:
    """pgvector accepts a string literal of the form '[v1,v2,...]'."""
    return "[" + ",".join(f"{v:.7f}" for v in vec) + "]"


def _agent_namespace(agent_name: str, namespace: str) -> str:
    """Namespaces are prefixed with the agent name so different agents don't collide.

    Example: agent_name='itc_commercial', namespace='leads' -> 'itc_commercial/leads'.
    The orchestrator can pass a fully-qualified namespace (containing '/') and
    we won't double-prefix.
    """
    namespace = (namespace or "default").strip()
    if "/" in namespace:
        return namespace
    return f"{agent_name}/{namespace}"


# ─────────────────────────────────────────────────────────────────────
# Public tool callables
# ─────────────────────────────────────────────────────────────────────


def store(
    agent_name: str,
    namespace: str,
    content: str,
    key: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
    ttl_days: Optional[int] = None,
) -> Dict[str, Any]:
    """Persist a memory item. Returns ``{memory_id, namespace, embedded}``.

    Parameters
    ----------
    agent_name: stable agent id (e.g. ``itc_commercial``). Used for namespacing.
    namespace: logical bucket (e.g. ``leads``, ``incidents/2026-q1``).
    content: the text to remember. Required.
    key: optional stable identifier (caller may upsert by reusing this).
    metadata: free-form JSON metadata.
    ttl_days: if set, the row gets ``expires_at = now() + ttl_days``. PII default.
    """
    if not content or not isinstance(content, str):
        raise ToolError("memory_store requires non-empty 'content' string")
    ns = _agent_namespace(agent_name, namespace)
    md = dict(metadata or {})
    md.setdefault("agent_name", agent_name)
    expires_at = None
    if ttl_days is not None and ttl_days > 0:
        expires_at = datetime.now(tz=timezone.utc) + timedelta(days=int(ttl_days))

    vec = _embed(content)
    vec_literal = _vector_literal(vec) if vec is not None else None

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_memory
                    (namespace, key, content, embedding, metadata_json, expires_at)
                VALUES (%s, %s, %s, %s::vector, %s::jsonb, %s)
                RETURNING memory_id
                """,
                (ns, key, content, vec_literal, json.dumps(md), expires_at),
            )
            row = cur.fetchone()
            if _PSYCOPG == "psycopg3":
                memory_id = row["memory_id"]
            else:
                memory_id = row[0]
                conn.commit()

    return {
        "memory_id": str(memory_id),
        "namespace": ns,
        "embedded": vec is not None,
        "expires_at": expires_at.isoformat() if expires_at else None,
    }


def retrieve(
    agent_name: str,
    namespace: str,
    query: str,
    k: int = 8,
) -> List[Dict[str, Any]]:
    """Fetch up to ``k`` memory items relevant to ``query``.

    Vector similarity is used when an embedding provider is configured;
    otherwise we ILIKE on content + order by recency.

    Returns a list of ``{memory_id, key, content, metadata, created_at, score}``.
    ``score`` is cosine distance (lower=closer) for vector mode, ``None`` for
    text fallback.
    """
    if not query or not isinstance(query, str):
        raise ToolError("memory_retrieve requires non-empty 'query' string")
    ns = _agent_namespace(agent_name, namespace)
    k = max(1, min(int(k or 8), 50))

    vec = _embed(query)

    with _conn() as conn:
        with conn.cursor() as cur:
            if vec is not None:
                cur.execute(
                    """
                    SELECT memory_id, key, content, metadata_json, created_at,
                           (embedding <=> %s::vector) AS score
                    FROM agent_memory
                    WHERE namespace = %s
                      AND embedding IS NOT NULL
                      AND (expires_at IS NULL OR expires_at > now())
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (_vector_literal(vec), ns, _vector_literal(vec), k),
                )
            else:
                like = f"%{query.strip()}%"
                cur.execute(
                    """
                    SELECT memory_id, key, content, metadata_json, created_at,
                           NULL::float8 AS score
                    FROM agent_memory
                    WHERE namespace = %s
                      AND (expires_at IS NULL OR expires_at > now())
                      AND content ILIKE %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (ns, like, k),
                )
            rows = cur.fetchall()

    out: List[Dict[str, Any]] = []
    for r in rows:
        if _PSYCOPG == "psycopg3":
            d = dict(r)
        else:
            # psycopg2 in tuple mode
            d = {
                "memory_id": r[0],
                "key": r[1],
                "content": r[2],
                "metadata_json": r[3],
                "created_at": r[4],
                "score": r[5],
            }
        out.append(
            {
                "memory_id": str(d["memory_id"]),
                "key": d.get("key"),
                "content": d.get("content"),
                "metadata": d.get("metadata_json") or {},
                "created_at": d["created_at"].isoformat() if d.get("created_at") else None,
                "score": float(d["score"]) if d.get("score") is not None else None,
            }
        )
    return out


# Public aliases — these names match the tool ids used in agent TOMLs.
memory_store = store
memory_retrieve = retrieve
