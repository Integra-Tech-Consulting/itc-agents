"""Gmail outbound tools — DRAFT and SEND.

Auth: OAuth refresh-token flow. Three env vars required:
  GMAIL_OAUTH_CLIENT_ID
  GMAIL_OAUTH_CLIENT_SECRET
  GMAIL_OAUTH_REFRESH_TOKEN

All `send` calls go through policy_check upstream (in the agent prompt).
This module does not check approval — it just performs the API call.
"""
from __future__ import annotations

import base64
import email.message
from typing import Any, Dict, List, Optional

import httpx

from ._base import ToolUpstreamError, http_json, require_env

_OAUTH_URL = "https://oauth2.googleapis.com/token"
_GMAIL_URL = "https://gmail.googleapis.com/gmail/v1/users/me"


def _access_token() -> str:
    with httpx.Client(timeout=15.0) as client:
        r = client.post(
            _OAUTH_URL,
            data={
                "client_id": require_env("GMAIL_OAUTH_CLIENT_ID"),
                "client_secret": require_env("GMAIL_OAUTH_CLIENT_SECRET"),
                "refresh_token": require_env("GMAIL_OAUTH_REFRESH_TOKEN"),
                "grant_type": "refresh_token",
            },
        )
    if r.status_code >= 400:
        raise ToolUpstreamError(f"oauth refresh -> {r.status_code}: {r.text[:300]}")
    return r.json()["access_token"]


def _build_raw(
    to: List[str], subject: str, body: str, cc: Optional[List[str]] = None
) -> str:
    msg = email.message.EmailMessage()
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def draft(
    to: List[str], subject: str, body: str, cc: Optional[List[str]] = None
) -> Dict[str, Any]:
    token = _access_token()
    return http_json(
        "POST",
        f"{_GMAIL_URL}/drafts",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"message": {"raw": _build_raw(to, subject, body, cc)}},
    )


def send(
    to: List[str], subject: str, body: str, cc: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Send an email. Caller MUST have obtained human approval first.

    Enforcement of `require_human_approval` happens in the agent layer
    via policy_check, not here.
    """
    token = _access_token()
    return http_json(
        "POST",
        f"{_GMAIL_URL}/messages/send",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"raw": _build_raw(to, subject, body, cc)},
    )
