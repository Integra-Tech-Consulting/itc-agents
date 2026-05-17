"""PayPal tools — invoicing + subscriptions + webhook verification.

API: https://developer.paypal.com/api/rest/
Auth: OAuth2 client_credentials. Required env vars:
  PAYPAL_CLIENT_ID
  PAYPAL_CLIENT_SECRET
  PAYPAL_MODE          ('live' or 'sandbox')
  PAYPAL_WEBHOOK_ID    (only for verify_webhook)
Optional:
  PAYPAL_API_BASE      (defaults derived from PAYPAL_MODE)

Hard rules mirrored from stripe_billing.py:
- We DO NOT expose payment capture, refund, or any direct money-movement
  endpoint. Invoicing + subscription read/cancel + webhook verify only.
- send_invoice is wrapped by `[guardrails].approval_required_for` in
  legal_finance.toml; this module performs the API call once approved.
- cancel_subscription is also human-gated (destructive).
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import httpx

from ._base import ToolUpstreamError, require_env

_LIVE_BASE = "https://api-m.paypal.com"
_SANDBOX_BASE = "https://api-m.sandbox.paypal.com"

# Cached access token (process-local; PayPal tokens last ~9h)
_TOKEN_CACHE: Dict[str, Any] = {"token": None, "exp": 0.0}


def _base_url() -> str:
    import os

    override = os.environ.get("PAYPAL_API_BASE", "").strip()
    if override:
        return override.rstrip("/")
    mode = (require_env("PAYPAL_MODE") or "").lower()
    if mode == "live":
        return _LIVE_BASE
    if mode == "sandbox":
        return _SANDBOX_BASE
    raise ToolUpstreamError(
        f"PAYPAL_MODE must be 'live' or 'sandbox' (got {mode!r})."
    )


def get_access_token() -> str:
    """OAuth2 client_credentials. Cached until ~60s before expiry."""
    now = time.time()
    if _TOKEN_CACHE["token"] and now < _TOKEN_CACHE["exp"]:
        return _TOKEN_CACHE["token"]

    client_id = require_env("PAYPAL_CLIENT_ID")
    client_secret = require_env("PAYPAL_CLIENT_SECRET")
    with httpx.Client(timeout=15.0) as client:
        r = client.post(
            f"{_base_url()}/v1/oauth2/token",
            auth=(client_id, client_secret),
            headers={"Accept": "application/json"},
            data={"grant_type": "client_credentials"},
        )
    if r.status_code >= 400:
        raise ToolUpstreamError(
            f"paypal oauth -> {r.status_code}: {r.text[:300]}"
        )
    data = r.json()
    token = data["access_token"]
    expires_in = int(data.get("expires_in", 3600))
    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["exp"] = now + max(60, expires_in - 60)
    return token


def _auth_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    h = {
        "Authorization": f"Bearer {get_access_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _request(
    method: str,
    path: str,
    *,
    json_body: Any = None,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    url = f"{_base_url()}{path}"
    with httpx.Client(timeout=20.0) as client:
        r = client.request(
            method,
            url,
            headers=_auth_headers(),
            json=json_body,
            params=params,
        )
    if r.status_code >= 400:
        raise ToolUpstreamError(
            f"paypal {method} {path} -> {r.status_code}: {r.text[:500]}"
        )
    if not r.content:
        return {}
    try:
        return r.json()
    except ValueError:
        return {"raw": r.text}


# ── Subscriptions ────────────────────────────────────────────────────


def get_subscription(subscription_id: str) -> Dict[str, Any]:
    """GET /v1/billing/subscriptions/{id} — read-only."""
    return _request("GET", f"/v1/billing/subscriptions/{subscription_id}")


def list_subscription_transactions(
    subscription_id: str,
    start_time: str,
    end_time: str,
) -> Dict[str, Any]:
    """List captured transactions for a subscription (read-only).

    start_time / end_time must be RFC 3339 timestamps.
    """
    return _request(
        "GET",
        f"/v1/billing/subscriptions/{subscription_id}/transactions",
        params={"start_time": start_time, "end_time": end_time},
    )


def cancel_subscription(subscription_id: str, reason: str) -> Dict[str, Any]:
    """Cancel an active subscription. DESTRUCTIVE — human-gated upstream.

    The agent layer enforces approval via policy_check; this just performs
    the API call.
    """
    if not reason or len(reason.strip()) < 5:
        raise ToolUpstreamError(
            "cancel_subscription requires a reason string (>= 5 chars)."
        )
    return _request(
        "POST",
        f"/v1/billing/subscriptions/{subscription_id}/cancel",
        json_body={"reason": reason.strip()[:127]},
    )


# ── Invoices ─────────────────────────────────────────────────────────


def list_invoices(page: int = 1, page_size: int = 20) -> Dict[str, Any]:
    """GET /v2/invoicing/invoices — read-only."""
    return _request(
        "GET",
        "/v2/invoicing/invoices",
        params={"page": page, "page_size": page_size, "total_required": "true"},
    )


def get_invoice(invoice_id: str) -> Dict[str, Any]:
    return _request("GET", f"/v2/invoicing/invoices/{invoice_id}")


def create_invoice_draft(
    recipient_email: str,
    items: List[Dict[str, Any]],
    *,
    currency: str = "EUR",
    note: str = "",
    invoice_number: Optional[str] = None,
    due_days: int = 30,
) -> Dict[str, Any]:
    """Create a DRAFT invoice. Does not send.

    `items` is a list of dicts:
      { "name": str, "quantity": str, "unit_amount": str }   # PayPal expects strings
    """
    if not items:
        raise ToolUpstreamError("create_invoice_draft requires at least one item.")

    detail: Dict[str, Any] = {
        "currency_code": currency,
        "note": note,
        "payment_term": {"term_type": "NET_30" if due_days == 30 else "DUE_ON_RECEIPT"},
    }
    if invoice_number:
        detail["invoice_number"] = invoice_number

    body = {
        "detail": detail,
        "primary_recipients": [
            {"billing_info": {"email_address": recipient_email}}
        ],
        "items": [
            {
                "name": str(it["name"]),
                "quantity": str(it.get("quantity", "1")),
                "unit_amount": {
                    "currency_code": currency,
                    "value": str(it["unit_amount"]),
                },
            }
            for it in items
        ],
    }
    return _request("POST", "/v2/invoicing/invoices", json_body=body)


def send_invoice(invoice_id: str, subject: Optional[str] = None) -> Dict[str, Any]:
    """Send a previously drafted invoice. Human-gated upstream.

    LIVE-mode large amounts (> €10,000) are blocked by the agent's
    `human_approval_required_for` policy entry, NOT here.
    """
    body: Dict[str, Any] = {"send_to_recipient": True}
    if subject:
        body["subject"] = subject
    return _request(
        "POST",
        f"/v2/invoicing/invoices/{invoice_id}/send",
        json_body=body,
    )


# ── Webhook verification ─────────────────────────────────────────────


def verify_webhook(
    headers: Dict[str, str],
    body: Any,
) -> Dict[str, Any]:
    """POST /v1/notifications/verify-webhook-signature.

    `headers` is the dict of incoming PayPal webhook headers
    (paypal-transmission-id / -time / -sig / cert-url / auth-algo).
    `body` is the parsed JSON event resource.
    """
    def _h(name: str) -> str:
        for k in (name, name.lower(), name.upper()):
            if k in headers and headers[k]:
                return headers[k]
        return ""

    payload = {
        "transmission_id": _h("paypal-transmission-id"),
        "transmission_time": _h("paypal-transmission-time"),
        "cert_url": _h("paypal-cert-url"),
        "auth_algo": _h("paypal-auth-algo"),
        "transmission_sig": _h("paypal-transmission-sig"),
        "webhook_id": require_env("PAYPAL_WEBHOOK_ID"),
        "webhook_event": body,
    }
    missing = [k for k, v in payload.items() if not v and k != "webhook_event"]
    if missing:
        raise ToolUpstreamError(
            f"verify_webhook missing headers: {missing}"
        )
    return _request(
        "POST",
        "/v1/notifications/verify-webhook-signature",
        json_body=payload,
    )
