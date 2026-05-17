"""Register the 10 ITC agents in a running OpenJarvis backend.

Idempotent: if an agent with the same name already exists, it is updated.
A registration manifest is written to deploy/.last-registration.json.

Usage:
    python deploy/register-with-jarvis.py --jarvis http://127.0.0.1:5055
    python deploy/register-with-jarvis.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any, Dict, List

import httpx

try:
    import tomllib  # py3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

ROOT = pathlib.Path(__file__).resolve().parent.parent
AGENTS_DIR = ROOT / "agents"
MANIFEST = ROOT / "deploy" / ".last-registration.json"

AGENTS_ORDER = [
    "orchestrator", "commercial", "delivery_pm", "engineer", "qa_sec",
    "data_analyst", "customer_success", "growth", "legal_finance", "governance",
]


def load_agent(name: str) -> Dict[str, Any]:
    path = AGENTS_DIR / f"{name}.toml"
    with path.open("rb") as f:
        return tomllib.load(f)


def to_jarvis_payload(name: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    tpl = spec["template"]
    config = {
        # template-level
        "system_prompt_template": tpl.get("system_prompt_template", ""),
        "tools": tpl.get("tools", []),
        "max_turns": tpl.get("max_turns", 15),
        "temperature": tpl.get("temperature", 0.2),
        "schedule_type": tpl.get("schedule_type", "event"),
        "schedule_value": tpl.get("schedule_value", "0"),
        "memory_extraction": tpl.get("memory_extraction", "scratchpad"),
        "observation_compression": tpl.get("observation_compression", "summarize"),
        "retrieval_strategy": tpl.get("retrieval_strategy", "sqlite"),
        "task_decomposition": tpl.get("task_decomposition", "monolithic"),
        # ITC extensions (Jarvis stores any extra keys as opaque JSON)
        "itc": {
            "version": "0.1.0",
            "handoffs": spec.get("handoffs", {}),
            "guardrails": spec.get("guardrails", {}),
            "kpis": spec.get("kpis", {}),
            "evals": spec.get("evals", {}),
            "memory_namespace": f"itc/{name}",
        },
    }
    return {
        "name": tpl["id"],
        "agent_type": tpl.get("agent_type", "monitor_operative"),
        "config": config,
    }


def find_existing(client: httpx.Client, jarvis: str, name: str) -> str | None:
    r = client.get(f"{jarvis}/v1/managed-agents", timeout=10)
    if r.status_code >= 400:
        return None
    for a in (r.json() or {}).get("agents", []) or []:
        if a.get("name") == name:
            return a.get("id")
    return None


def register_one(
    client: httpx.Client, jarvis: str, name: str, payload: Dict[str, Any], dry: bool
) -> Dict[str, Any]:
    existing = find_existing(client, jarvis, payload["name"])
    if dry:
        return {"name": payload["name"], "action": "would_update" if existing else "would_create"}

    if existing:
        r = client.patch(
            f"{jarvis}/v1/managed-agents/{existing}",
            json={"agent_type": payload["agent_type"], "config": payload["config"]},
            timeout=20,
        )
        action = "updated"
    else:
        r = client.post(f"{jarvis}/v1/managed-agents", json=payload, timeout=20)
        action = "created"

    if r.status_code >= 400:
        return {"name": payload["name"], "action": "ERROR", "status": r.status_code, "body": r.text[:500]}
    data = r.json() or {}
    return {
        "name": payload["name"],
        "action": action,
        "id": data.get("id") or existing,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--jarvis", default="http://127.0.0.1:5055")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--only", help="comma-separated list of agent names to register")
    args = p.parse_args()

    only = set((args.only or "").split(",")) if args.only else None
    results: List[Dict[str, Any]] = []

    with httpx.Client() as client:
        # smoke-check Jarvis is up
        try:
            h = client.get(f"{args.jarvis}/health", timeout=5)
            h.raise_for_status()
        except Exception as e:
            print(f"FATAL: Jarvis not reachable at {args.jarvis}: {e}", file=sys.stderr)
            return 2

        for name in AGENTS_ORDER:
            if only and name not in only:
                continue
            spec = load_agent(name)
            payload = to_jarvis_payload(name, spec)
            res = register_one(client, args.jarvis, name, payload, args.dry_run)
            print(json.dumps(res))
            results.append(res)

    MANIFEST.write_text(json.dumps(results, indent=2), encoding="utf-8")
    errors = [r for r in results if r.get("action") == "ERROR"]
    print(f"\nDone. {len(results)} agents processed, {len(errors)} errors. Manifest at {MANIFEST}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
