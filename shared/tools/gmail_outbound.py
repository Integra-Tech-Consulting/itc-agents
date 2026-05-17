"""Gmail outbound tools — DRAFT and SEND.

Auth: OAuth refresh-token flow. Credentials are resolved in this order:

  1. **OpenJarvis connector store** at ``~/.openjarvis/connectors/gmail.json``
     (the file Jarvis writes after a successful OAuth handshake). Expected
     keys: ``client_id``, ``client_secret``, ``refresh_token``. This is the
     preferred path — Jarvis already manages the OAuth flow, we just consume
     the stored refresh token. The path can be overridden via
     ``OPENJARVIS_CONNECTORS_DIR``.
  2. Environment variables ``GMAIL_OAUTH_CLIENT_ID`` / ``GMAIL_OAUTH_CLIENT_SECRET``
     / ``GMAIL_OAUTH_REFRESH_TOKEN`` (fallback for non-Jarvis hosts).

IMPORTANT: the default Jarvis Gmail connector is granted ``gmail.readonly``
scope, which is NOT sufficient for sending. To send, the OAuth consent that
populated ``gmail.json`` must include ``https://www.googleapis.com/auth/gmail.send``.
If the scope is wrong, the Gmail API call below returns 403 and that error
surfaces to the agent — we never silently degrade or fall back to a mock.

All `send` calls go through policy_check upstream (in the agent prompt).
This module does not check approval — it just performs the API call.
"""
from __future__ import annotations

import base64
import email.message
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ._base import ToolCredentialError, ToolUpstreamError, http_json, require_env

_OAUTH_URL = "https://oauth2.googleapis.com/token"
_GMAIL_URL = "https://gmail.googleapis.com/gmail/v1/users/me"


def _jarvis_connectors_dir() -> Path:
    override = os.environ.get("OPENJARVIS_CONNECTORS_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".openjarvis" / "connectors"


def _load_jarvis_creds() -> Optional[Tuple[str, str, str]]:
    """Return (client_id, client_secret, refresh_token) from Jarvis store.

    Returns None if the gmail.json file does not exist or lacks any of the
    required keys (caller will then fall back to env vars).
    """
    path = _jarvis_connectors_dir() / "gmail.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    client_id = (data.get("client_id") or "").strip()
    client_secret = (data.get("client_secret") or "").strip()
    refresh_token = (data.get("refresh_token") or "").strip()
    if not (client_id and client_secret and refresh_token):
        return None
    return client_id, client_secret, refresh_token


def _resolve_oauth_creds() -> Tuple[str, str, str]:
    """Resolve OAuth creds: Jarvis store first, env vars second."""
    jarvis = _load_jarvis_creds()
    if jarvis is not None:
        return jarvis

    # Env-var fallback. Collect missing names to give one clear error.
    cid = os.environ.get("GMAIL_OAUTH_CLIENT_ID", "").strip()
    csec = os.environ.get("GMAIL_OAUTH_CLIENT_SECRET", "").strip()
    rtok = os.environ.get("GMAIL_OAUTH_REFRESH_TOKEN", "").strip()
    missing = [
        n
        for n, v in (
            ("GMAIL_OAUTH_CLIENT_ID", cid),
            ("GMAIL_OAUTH_CLIENT_SECRET", csec),
            ("GMAIL_OAUTH_REFRESH_TOKEN", rtok),
        )
        if not v
    ]
    if missing:
        raise ToolCredentialError(
            "No Gmail OAuth credentials found. Either complete the OAuth "
            "flow in OpenJarvis (with the gmail.send scope) so that "
            f"{_jarvis_connectors_dir() / 'gmail.json'} is populated, "
            f"or set env vars: {', '.join(missing)}."
        )
    return cid, csec, rtok


def _access_token() -> str:
    client_id, client_secret, refresh_token = _resolve_oauth_creds()
    with httpx.Client(timeout=15.0) as client:
        r = client.post(
            _OAUTH_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
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
