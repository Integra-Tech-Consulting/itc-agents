"""Linear tools (GraphQL).

API: https://developers.linear.app
Auth: LINEAR_API_KEY (personal API key).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ._base import http_json, require_env

_URL = "https://api.linear.app/graphql"


def _headers() -> Dict[str, str]:
    return {
        "Authorization": require_env("LINEAR_API_KEY"),
        "Content-Type": "application/json",
    }


def _gql(query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return http_json(
        "POST",
        _URL,
        headers=_headers(),
        json={"query": query, "variables": variables or {}},
    )


def create_project(team_id: str, name: str, description: str = "") -> Dict[str, Any]:
    q = """mutation($input: ProjectCreateInput!) {
        projectCreate(input: $input) { success project { id name url } }
    }"""
    return _gql(q, {"input": {"teamIds": [team_id], "name": name, "description": description}})


def create_issue(
    team_id: str,
    title: str,
    description: str = "",
    *,
    project_id: Optional[str] = None,
    assignee_id: Optional[str] = None,
    priority: int = 3,
    estimate: Optional[int] = None,
) -> Dict[str, Any]:
    inp: Dict[str, Any] = {
        "teamId": team_id,
        "title": title,
        "description": description,
        "priority": priority,
    }
    if project_id:
        inp["projectId"] = project_id
    if assignee_id:
        inp["assigneeId"] = assignee_id
    if estimate is not None:
        inp["estimate"] = estimate
    q = """mutation($input: IssueCreateInput!) {
        issueCreate(input: $input) { success issue { id identifier url } }
    }"""
    return _gql(q, {"input": inp})


def update_issue(issue_id: str, **fields: Any) -> Dict[str, Any]:
    q = """mutation($id: String!, $input: IssueUpdateInput!) {
        issueUpdate(id: $id, input: $input) { success issue { id identifier state { name } } }
    }"""
    return _gql(q, {"id": issue_id, "input": fields})


def list_issues(team_id: str, state: Optional[str] = None) -> Dict[str, Any]:
    filt: Dict[str, Any] = {"team": {"id": {"eq": team_id}}}
    if state:
        filt["state"] = {"name": {"eq": state}}
    q = """query($filter: IssueFilter) {
        issues(filter: $filter, first: 50) {
            nodes { id identifier title state { name } assignee { name } priority }
        }
    }"""
    return _gql(q, {"filter": filt})
