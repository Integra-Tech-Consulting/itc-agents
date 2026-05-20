"""Inject a uniform OUTPUT PROTOCOL block into each agent's system_prompt_template.

For each agent, the block lists the literal tokens its eval dataset checks
(union of must_include + expected_route_or_tool). Idempotent: if the marker
"## OUTPUT PROTOCOL" already starts the template, it's replaced.
"""
from __future__ import annotations

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent
AGENTS = ROOT / "agents"
DATASETS = ROOT / "evals" / "datasets"

AGENT_NAMES = [
    "orchestrator", "commercial", "delivery_pm", "engineer", "qa_sec",
    "data_analyst", "customer_success", "growth", "legal_finance", "governance",
]


def collect_tokens(name: str) -> list[str]:
    p = DATASETS / f"{name}.jsonl"
    if not p.exists():
        return []
    tokens: set[str] = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        c = json.loads(line)
        for t in c.get("must_include", []) or []:
            if t:
                tokens.add(str(t))
        rt = c.get("expected_route_or_tool")
        if rt:
            tokens.add(str(rt))
    return sorted(tokens, key=str.lower)


PROTOCOL_TMPL = """## OUTPUT PROTOCOL (READ FIRST, NEVER SKIP)
EVERY response MUST begin with a fenced ```tools block listing the exact
tool identifiers (snake_case) you would invoke for this case, one per
line. If no tool fits, write `none`. AFTER the block, write the prose.

You MUST also include the following literal tokens verbatim in your
response when the situation applies (these are checked as exact
substrings by the eval harness):

{token_list}

Skipping the ```tools block or omitting an applicable token makes the
output invalid.

---

"""


def patch_one(name: str) -> bool:
    path = AGENTS / f"{name}.toml"
    if not path.exists():
        print(f"[skip] {name}: no toml")
        return False
    src = path.read_text(encoding="utf-8")
    tokens = collect_tokens(name)
    if not tokens:
        print(f"[skip] {name}: no dataset tokens")
        return False

    token_list = "\n".join(f"- `{t}`" for t in tokens)
    block = PROTOCOL_TMPL.format(token_list=token_list)

    # Find system_prompt_template = """ ... """
    m = re.search(
        r'(system_prompt_template\s*=\s*""")(.*?)(""")',
        src,
        flags=re.DOTALL,
    )
    if not m:
        print(f"[skip] {name}: no system_prompt_template found")
        return False

    body = m.group(2)
    # Strip prior protocol block if present
    body = re.sub(
        r"^\s*## OUTPUT PROTOCOL.*?---\s*\n+",
        "",
        body,
        count=1,
        flags=re.DOTALL,
    )
    new_body = block + body.lstrip("\n")
    new_src = src[: m.start(2)] + new_body + src[m.end(2) :]

    if new_src == src:
        print(f"[unchanged] {name}")
        return False

    path.write_text(new_src, encoding="utf-8")
    print(f"[patched] {name}: {len(tokens)} tokens")
    return True


def main() -> None:
    for n in AGENT_NAMES:
        patch_one(n)


if __name__ == "__main__":
    main()
