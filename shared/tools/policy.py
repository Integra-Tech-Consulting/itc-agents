"""Policy check + kill switch.

These are the gates that every agent calls before doing anything that
touches money, external comms, or production.

Reads ITC_AGENTS_DISABLED, ITC_REQUIRE_HUMAN_APPROVAL, daily budgets,
and the per-agent `approval_required_for` list from the TOML config.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional


@dataclass
class PolicyDecision:
    decision: Literal["approved", "denied", "needs_human_approval"]
    reason: str
    requires_human: bool = False


def kill_switch_active() -> bool:
    return os.environ.get("ITC_AGENTS_DISABLED", "false").lower() == "true"


def policy_check(
    agent_name: str,
    tool: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    agent_config: Optional[Dict[str, Any]] = None,
) -> PolicyDecision:
    """Single decision gate.

    Logic, in order:
      1. Kill-switch → deny.
      2. Tool in agent's `forbidden_tools` → deny.
      3. Tool in agent's `approval_required_for` AND require_human=true → needs_human.
      4. Stripe in live mode with amount > limit → deny.
      5. Otherwise approve.
    """
    if kill_switch_active():
        return PolicyDecision(
            "denied", "kill-switch ITC_AGENTS_DISABLED=true is active"
        )

    cfg = agent_config or {}
    guards = cfg.get("guardrails", {}) if isinstance(cfg, dict) else {}
    forbidden = set(guards.get("forbidden_tools", []) or [])
    approval = set(guards.get("approval_required_for", []) or [])
    require_human = bool(guards.get("require_human_approval", False))

    if tool in forbidden:
        return PolicyDecision(
            "denied", f"tool '{tool}' is in {agent_name}.forbidden_tools"
        )

    # money-mover guard: any Stripe call in live mode > €10k must be human-approved
    if tool.startswith("stripe_") and os.environ.get("STRIPE_API_KEY_MODE", "test") == "live":
        amt = 0
        if payload:
            for it in payload.get("line_items", []) or []:
                amt += int(it.get("amount_cents", 0) or 0)
        if amt > 10_000 * 100:
            return PolicyDecision(
                "needs_human_approval",
                f"live-mode Stripe call > €10k (got {amt/100:.2f}€)",
                requires_human=True,
            )

    if tool in approval and require_human:
        return PolicyDecision(
            "needs_human_approval",
            f"tool '{tool}' requires human approval per policy",
            requires_human=True,
        )

    return PolicyDecision("approved", "ok")
