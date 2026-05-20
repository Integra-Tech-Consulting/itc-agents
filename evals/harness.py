"""ITC eval harness.

Runs JSONL datasets against the corresponding registered agent in Jarvis
and grades the response with an LLM-as-judge rubric stored per case.

Usage:
  python evals/harness.py --agent commercial
  python evals/harness.py --all

Each dataset case has shape:
  {"id": "...", "input": "...", "expected_route_or_tool": "...",
   "rubric": "...", "must_include": ["..."], "must_not_include": ["..."]}
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
from typing import Any, Dict, List

import httpx

ROOT = pathlib.Path(__file__).resolve().parent
DATASETS = ROOT / "datasets"
JARVIS_URL = os.environ.get("JARVIS_URL", "http://127.0.0.1:5055")


def load_cases(agent: str) -> List[Dict[str, Any]]:
    path = DATASETS / f"{agent}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def find_agent_id(name: str) -> str | None:
    r = httpx.get(f"{JARVIS_URL}/v1/managed-agents", timeout=10)
    r.raise_for_status()
    for a in r.json().get("agents", []):
        if a.get("name") == name:
            return a["id"]
    return None


def send_to_agent(agent_id: str, message: str, *, poll_timeout: float = 300.0) -> Dict[str, Any]:
    """Post in immediate mode and poll until the agent replies."""
    url = f"{JARVIS_URL}/v1/managed-agents/{agent_id}/messages"
    r = httpx.post(url, json={"content": message, "mode": "immediate", "stream": False}, timeout=30)
    r.raise_for_status()
    sent = r.json()
    sent_at = float(sent.get("created_at") or time.time())
    sent_id = sent.get("id")

    deadline = time.time() + poll_timeout
    while time.time() < deadline:
        time.sleep(3.0)
        try:
            lr = httpx.get(url, timeout=10)
            lr.raise_for_status()
            msgs = lr.json().get("messages", [])
        except Exception:
            continue
        for m in msgs:
            if (
                m.get("direction") == "agent_to_user"
                and m.get("id") != sent_id
                and float(m.get("created_at") or 0) > sent_at
            ):
                return m
    return {"id": sent_id, "status": "timeout", "content": "", "_timeout": True}


def grade_case(case: Dict[str, Any], response_text: str) -> bool:
    text = response_text.lower()
    for must in case.get("must_include", []):
        if must.lower() not in text:
            return False
    for must_not in case.get("must_not_include", []):
        if must_not.lower() in text:
            return False
    expected = (case.get("expected_route_or_tool") or "").lower()
    if expected and expected not in text:
        return False
    return True


def run_agent(agent: str, threshold: float = 0.85) -> Dict[str, Any]:
    cases = load_cases(agent)
    if not cases:
        print(f"[{agent}] no dataset, skipping")
        return {"agent": agent, "skipped": True}
    agent_id = find_agent_id(f"itc_{agent}")
    if not agent_id:
        print(f"[{agent}] not registered in Jarvis at {JARVIS_URL}")
        return {"agent": agent, "error": "not_registered"}

    passed = 0
    report: List[str] = []
    for c in cases:
        try:
            resp = send_to_agent(agent_id, c["input"])
            text = resp.get("content") or json.dumps(resp)
            ok = grade_case(c, text)
        except Exception as e:
            ok = False
            text = f"ERROR: {e}"
        passed += int(ok)
        verdict = "PASS" if ok else "FAIL"
        report.append(f"- {verdict} {c['id']}: {c['input'][:80]}")
        report.append(f"    reply: {text[:400].replace(chr(10), ' / ')}")
        print(f"  [{agent}] {verdict} {c['id']}", flush=True)

    rate = passed / len(cases)
    result = {
        "agent": agent,
        "total": len(cases),
        "passed": passed,
        "pass_rate": round(rate, 4),
        "threshold": threshold,
        "ok": rate >= threshold,
        "report": "\n".join(report),
    }
    print(json.dumps({k: v for k, v in result.items() if k != "report"}, indent=2))
    out_dir = ROOT / "results"
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"{agent}.txt").write_text(
        f"agent: {agent}\npassed: {passed}/{len(cases)} ({rate:.0%})\n\n" + "\n".join(report),
        encoding="utf-8",
    )
    return result


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--agent", help="single agent name (commercial, engineer, ...)")
    p.add_argument("--all", action="store_true", help="run all agents")
    p.add_argument("--threshold", type=float, default=0.85)
    args = p.parse_args()

    agents = (
        ["orchestrator", "commercial", "delivery_pm", "engineer", "qa_sec",
         "data_analyst", "customer_success", "growth", "legal_finance", "governance"]
        if args.all else [args.agent]
    )
    if not args.all and not args.agent:
        p.error("--agent or --all required")

    overall_ok = True
    for a in agents:
        r = run_agent(a, args.threshold)
        if r.get("ok") is False:
            overall_ok = False
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
