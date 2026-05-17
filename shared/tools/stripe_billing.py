"""Stripe tools — invoice + dunning ONLY. Never charge.

API: https://stripe.com/docs/api
Auth: STRIPE_API_KEY. STRIPE_API_KEY_MODE must be 'test' or 'live'.
Charging APIs (PaymentIntents, Charges) are NOT exposed by design.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from ._base import ToolUpstreamError, require_env

_BASE = "https://api.stripe.com/v1"


def _auth() -> tuple[str, str]:
    return (require_env("STRIPE_API_KEY"), "")


def _check_mode_safe() -> None:
    mode = (require_env("STRIPE_API_KEY_MODE") or "").lower()
    if mode not in ("test", "live"):
        raise ToolUpstreamError(
            "STRIPE_API_KEY_MODE must be 'test' or 'live'."
        )


def create_invoice(
    customer_id: str,
    line_items: List[Dict[str, Any]],
    *,
    days_until_due: int = 30,
    metadata: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Create draft invoice + invoice items. Returns the invoice resource.

    Does NOT finalize or send. Human approval needed for that.
    """
    _check_mode_safe()
    with httpx.Client(auth=_auth(), timeout=20.0) as client:
        for item in line_items:
            r = client.post(
                f"{_BASE}/invoiceitems",
                data={
                    "customer": customer_id,
                    "amount": int(item["amount_cents"]),
                    "currency": item.get("currency", "eur"),
                    "description": item.get("description", ""),
                },
            )
            if r.status_code >= 400:
                raise ToolUpstreamError(
                    f"invoiceitems -> {r.status_code}: {r.text[:300]}"
                )
        inv_data = {
            "customer": customer_id,
            "collection_method": "send_invoice",
            "days_until_due": days_until_due,
            "auto_advance": "false",
        }
        for k, v in (metadata or {}).items():
            inv_data[f"metadata[{k}]"] = v
        r = client.post(f"{_BASE}/invoices", data=inv_data)
        if r.status_code >= 400:
            raise ToolUpstreamError(
                f"invoices -> {r.status_code}: {r.text[:300]}"
            )
        return r.json()


def list_invoices(customer_id: Optional[str] = None, status: str = "open") -> Dict[str, Any]:
    params = {"status": status, "limit": "100"}
    if customer_id:
        params["customer"] = customer_id
    with httpx.Client(auth=_auth(), timeout=20.0) as client:
        r = client.get(f"{_BASE}/invoices", params=params)
    if r.status_code >= 400:
        raise ToolUpstreamError(f"list_invoices -> {r.status_code}: {r.text[:300]}")
    return r.json()


def send_reminder(invoice_id: str) -> Dict[str, Any]:
    """Send the Stripe-managed payment reminder for a finalized invoice."""
    with httpx.Client(auth=_auth(), timeout=20.0) as client:
        r = client.post(f"{_BASE}/invoices/{invoice_id}/send")
    if r.status_code >= 400:
        raise ToolUpstreamError(f"send_reminder -> {r.status_code}: {r.text[:300]}")
    return r.json()
