"""Shared tool errors and a thin HTTP helper.

All ITC agent tools share two principles:
1. **No mocks.** If a credential is missing or an API returns an error,
   we raise `ToolCredentialError` or `ToolUpstreamError` immediately.
   Agents see the error and must handle it; they NEVER receive a
   fabricated response.
2. **Idempotent and small.** Each tool function does one HTTP call and
   returns the parsed JSON. Composition lives in agent prompts, not here.
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Optional

import httpx


class ToolError(RuntimeError):
    """Base class for tool-side failures surfaced to the agent."""


class ToolCredentialError(ToolError):
    """A required env var is missing or empty."""


class ToolUpstreamError(ToolError):
    """The upstream API returned a non-2xx response."""


def require_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise ToolCredentialError(
            f"Missing credential: {name}. Set it in secrets.env. "
            "This tool does NOT fall back to a mock."
        )
    return val


def http_json(
    method: str,
    url: str,
    *,
    headers: Optional[Mapping[str, str]] = None,
    json: Any = None,
    params: Optional[Mapping[str, Any]] = None,
    timeout: float = 20.0,
) -> Any:
    """Single source of truth for HTTP calls from tools.

    Raises ToolUpstreamError on non-2xx. Returns parsed JSON (or {} if empty).
    """
    with httpx.Client(timeout=timeout) as client:
        resp = client.request(
            method, url, headers=headers, json=json, params=params
        )
    if resp.status_code >= 400:
        raise ToolUpstreamError(
            f"{method} {url} -> {resp.status_code}: {resp.text[:500]}"
        )
    if not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text}
