"""HubSpot tools (real CRM, no mocks).

API docs: https://developers.hubspot.com/docs/api/overview
Auth: Private app token via HUBSPOT_API_KEY.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ._base import http_json, require_env

_BASE = "https://api.hubapi.com"


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {require_env('HUBSPOT_API_KEY')}",
        "Content-Type": "application/json",
    }


def search_contact(email: str) -> Dict[str, Any]:
    return http_json(
        "POST",
        f"{_BASE}/crm/v3/objects/contacts/search",
        headers=_headers(),
        json={
            "filterGroups": [
                {"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}
            ],
            "limit": 1,
        },
    )


def create_contact(email: str, properties: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = {"properties": {"email": email, **(properties or {})}}
    return http_json(
        "POST",
        f"{_BASE}/crm/v3/objects/contacts",
        headers=_headers(),
        json=payload,
    )


def update_deal(deal_id: str, properties: Dict[str, Any]) -> Dict[str, Any]:
    return http_json(
        "PATCH",
        f"{_BASE}/crm/v3/objects/deals/{deal_id}",
        headers=_headers(),
        json={"properties": properties},
    )


def log_activity(
    contact_id: str, activity_type: str, note: str
) -> Dict[str, Any]:
    payload = {
        "properties": {
            "hs_note_body": note,
            "hs_timestamp": None,  # server-side now()
        },
        "associations": [
            {
                "to": {"id": contact_id},
                "types": [
                    {"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}
                ],
            }
        ],
    }
    if activity_type != "note":
        # could extend to tasks/emails/meetings; for now we always log a note.
        payload["properties"]["hs_note_body"] = f"[{activity_type}] {note}"
    return http_json(
        "POST",
        f"{_BASE}/crm/v3/objects/notes",
        headers=_headers(),
        json=payload,
    )
