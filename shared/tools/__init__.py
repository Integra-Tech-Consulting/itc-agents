"""Public tool registry — agents reference tools by name in their TOML.

Each entry maps a tool name (as used in agent prompts/configs) to the
Python callable that implements it. The registration script reads this
to validate that every tool referenced by an agent has an implementation.
"""
from __future__ import annotations

from typing import Callable, Dict

from . import (
    crm_hubspot,
    github_admin,
    gmail_outbound,
    linear_pm,
    paypal_billing,
    stripe_billing,
)
from .policy import policy_check

REGISTRY: Dict[str, Callable] = {
    # HubSpot
    "hubspot_search_contact": crm_hubspot.search_contact,
    "hubspot_create_contact": crm_hubspot.create_contact,
    "hubspot_update_deal": crm_hubspot.update_deal,
    "hubspot_log_activity": crm_hubspot.log_activity,
    # Stripe
    "stripe_create_invoice": stripe_billing.create_invoice,
    "stripe_list_invoices": stripe_billing.list_invoices,
    "stripe_send_reminder": stripe_billing.send_reminder,
    # PayPal (invoice + subscription read/cancel + webhook verify; no capture/refund)
    "paypal_create_invoice_draft": paypal_billing.create_invoice_draft,
    "paypal_send_invoice": paypal_billing.send_invoice,
    "paypal_list_invoices": paypal_billing.list_invoices,
    "paypal_get_invoice": paypal_billing.get_invoice,
    "paypal_get_subscription": paypal_billing.get_subscription,
    "paypal_list_subscription_transactions": paypal_billing.list_subscription_transactions,
    "paypal_cancel_subscription": paypal_billing.cancel_subscription,
    "paypal_verify_webhook": paypal_billing.verify_webhook,
    # Linear
    "linear_create_project": linear_pm.create_project,
    "linear_create_issue": linear_pm.create_issue,
    "linear_update_issue": linear_pm.update_issue,
    "linear_list_issues": linear_pm.list_issues,
    # Gmail
    "gmail_draft": gmail_outbound.draft,
    "gmail_send": gmail_outbound.send,
    # GitHub
    "github_create_pr": github_admin.create_pr,
    "github_comment": github_admin.comment_on_pr,
    "github_request_changes": github_admin.request_changes,
    "github_approve": github_admin.approve,
    # Policy
    "policy_check": policy_check,
}


def is_registered(name: str) -> bool:
    return name in REGISTRY


def builtins() -> set[str]:
    """Tool names that Jarvis provides natively (don't need a Python impl here)."""
    return {
        "file_read",
        "file_write",
        "shell_exec",
        "git_status",
        "git_diff",
        "git_commit",
        "git_log",
        "apply_patch",
        "code_interpreter",
        "memory_store",
        "memory_retrieve",
        "think",
        "agent_handoff",
        "agent_status",
        "agent_pause",
        "kill_switch",
        "calendar_create_event",
        "sql_query",
        "web_fetch",
    }
