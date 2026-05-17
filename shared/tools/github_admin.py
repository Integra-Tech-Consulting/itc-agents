"""GitHub admin tools.

Auth: GITHUB_BOT_TOKEN with scopes `repo`, `read:org`. Used for PR
review, comments, org admin (archive, etc.). Does NOT perform org-wide
deletes — those are excluded by design.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ._base import http_json, require_env

_BASE = "https://api.github.com"


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {require_env('GITHUB_BOT_TOKEN')}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def create_pr(
    owner: str, repo: str, title: str, head: str, base: str, body: str = ""
) -> Dict[str, Any]:
    return http_json(
        "POST",
        f"{_BASE}/repos/{owner}/{repo}/pulls",
        headers=_headers(),
        json={"title": title, "head": head, "base": base, "body": body},
    )


def comment_on_pr(owner: str, repo: str, pr_number: int, body: str) -> Dict[str, Any]:
    return http_json(
        "POST",
        f"{_BASE}/repos/{owner}/{repo}/issues/{pr_number}/comments",
        headers=_headers(),
        json={"body": body},
    )


def request_changes(
    owner: str, repo: str, pr_number: int, body: str
) -> Dict[str, Any]:
    return http_json(
        "POST",
        f"{_BASE}/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
        headers=_headers(),
        json={"event": "REQUEST_CHANGES", "body": body},
    )


def approve(
    owner: str, repo: str, pr_number: int, body: str = "LGTM"
) -> Dict[str, Any]:
    return http_json(
        "POST",
        f"{_BASE}/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
        headers=_headers(),
        json={"event": "APPROVE", "body": body},
    )


def archive_repo(owner: str, repo: str) -> Dict[str, Any]:
    """Reversible. Use unarchive_repo() to undo."""
    return http_json(
        "PATCH",
        f"{_BASE}/repos/{owner}/{repo}",
        headers=_headers(),
        json={"archived": True},
    )


def unarchive_repo(owner: str, repo: str) -> Dict[str, Any]:
    return http_json(
        "PATCH",
        f"{_BASE}/repos/{owner}/{repo}",
        headers=_headers(),
        json={"archived": False},
    )


def update_readme(
    owner: str, repo: str, content: str, message: str, sha: Optional[str] = None
) -> Dict[str, Any]:
    import base64

    body: Dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
    }
    if sha:
        body["sha"] = sha
    return http_json(
        "PUT",
        f"{_BASE}/repos/{owner}/{repo}/contents/README.md",
        headers=_headers(),
        json=body,
    )


def get_readme(owner: str, repo: str) -> Dict[str, Any]:
    return http_json(
        "GET",
        f"{_BASE}/repos/{owner}/{repo}/contents/README.md",
        headers=_headers(),
    )
