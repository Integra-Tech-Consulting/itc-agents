"""Archive every `agent-itc-*` repo in the Integra-Tech-Consulting org and
prepend a deprecation banner to each README pointing to the new agent.

Idempotent. By default DRY-RUN. Add --confirm to actually mutate.

Auth: uses `gh` CLI (already authenticated as MutenRos with `repo` scope).
We shell out to `gh` instead of using a raw token, because that token is
already on disk and we don't want a second one.

Usage:
    python deploy/archive-legacy-repos.py            # dry-run, prints plan
    python deploy/archive-legacy-repos.py --confirm  # actually archive + banner
    python deploy/archive-legacy-repos.py --confirm --no-banner  # only archive
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time
from typing import Any, Dict, List

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAPPING_PATH = ROOT / "deploy" / "role-mapping.json"
ORG = "Integra-Tech-Consulting"
NEW_REPO_URL = f"https://github.com/{ORG}/itc-agents"

DEPT_RE = re.compile(r"^agent-itc-([A-Z]+)-")


def gh(*args: str, capture: bool = True) -> str:
    """Run gh CLI. Raises on failure."""
    r = subprocess.run(
        ["gh", *args],
        capture_output=capture,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} -> {r.returncode}\n{r.stderr}")
    return r.stdout


def list_legacy_repos() -> List[Dict[str, Any]]:
    out = gh(
        "repo", "list", ORG, "--limit", "500",
        "--json", "name,isArchived,description",
    )
    repos = json.loads(out)
    return [r for r in repos if r["name"].startswith("agent-itc-")]


def dept_for(repo: str, mapping: Dict[str, str]) -> str:
    m = DEPT_RE.match(repo)
    code = m.group(1) if m else ""
    return mapping.get(code, mapping["_default"])


def banner(repo: str, new_agent: str) -> str:
    return f"""# [ARCHIVED] {repo}

> This repository was part of an empty scaffolding of 166 single-line repos.
> It has been **archived (read-only)** on {time.strftime('%Y-%m-%d')} and superseded
> by the consolidated **itc-agents** monorepo.
>
> **Role absorbed by:** `{new_agent}` in [`{ORG}/itc-agents`]({NEW_REPO_URL})
>
> See `{NEW_REPO_URL}/blob/main/agents/{new_agent}.toml` for the active agent
> definition (system prompt, tools, handoffs, guardrails, KPIs, evals).

---

_Historical role description below for reference only._
"""


def update_readme(repo: str, new_agent: str) -> None:
    """Replace README.md with deprecation banner + a tiny role hint."""
    body = banner(repo, new_agent)
    # gh api supports content as base64, but `gh repo edit` does not write
    # files. Use the contents API directly via `gh api`.
    # First: get existing README sha (if any).
    try:
        existing = json.loads(
            gh("api", f"/repos/{ORG}/{repo}/contents/README.md")
        )
        sha = existing.get("sha")
    except Exception:
        sha = None

    import base64
    payload = {
        "message": "Archive: superseded by itc-agents monorepo",
        "content": base64.b64encode(body.encode("utf-8")).decode("ascii"),
    }
    if sha:
        payload["sha"] = sha

    # Write to a temp JSON file because gh api -F escaping is tricky.
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(payload, f)
        tmp = f.name
    try:
        gh("api", "--method", "PUT", f"/repos/{ORG}/{repo}/contents/README.md",
           "--input", tmp)
    finally:
        pathlib.Path(tmp).unlink(missing_ok=True)


def archive(repo: str) -> None:
    gh("api", "--method", "PATCH", f"/repos/{ORG}/{repo}",
       "-f", "archived=true")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--confirm", action="store_true", help="actually mutate")
    p.add_argument("--no-banner", action="store_true",
                   help="skip README banner update; only archive")
    p.add_argument("--limit", type=int, default=0, help="cap repos (0=all)")
    args = p.parse_args()

    mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    repos = list_legacy_repos()
    if args.limit:
        repos = repos[: args.limit]

    print(f"Found {len(repos)} agent-itc-* repos in {ORG} "
          f"({sum(1 for r in repos if r['isArchived'])} already archived).")
    print(f"Mode: {'CONFIRM (will mutate)' if args.confirm else 'DRY-RUN'}\n")

    plan: List[Dict[str, Any]] = []
    for r in repos:
        plan.append({"repo": r["name"], "to": dept_for(r["name"], mapping),
                     "already_archived": r["isArchived"]})

    # show first 10 + summary
    for row in plan[:10]:
        print(f"  {row['repo']:55s} -> {row['to']:18s} "
              f"{'(already archived)' if row['already_archived'] else ''}")
    if len(plan) > 10:
        print(f"  ... and {len(plan) - 10} more")

    if not args.confirm:
        print("\nDRY-RUN. Re-run with --confirm to apply.")
        return 0

    ok = 0
    fail = 0
    for row in plan:
        repo = row["repo"]
        target = row["to"]
        try:
            if not args.no_banner:
                update_readme(repo, target)
            if not row["already_archived"]:
                archive(repo)
            ok += 1
            if ok % 10 == 0:
                print(f"  {ok}/{len(plan)} done...")
        except Exception as e:
            fail += 1
            print(f"  FAIL {repo}: {e}")

    print(f"\nDone. ok={ok} fail={fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
