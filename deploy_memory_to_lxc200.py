"""One-shot deploy of the new memory tools + requirements to LXC 200.

Path: dev machine -> PVE host (192.168.1.9) -> LXC 200 (/opt/itc-agents).
Reads PVE root password from PVE_ROOT_PASS env or prompts.

Usage:
    python deploy_memory_to_lxc200.py
"""
from __future__ import annotations

import os
import sys
import getpass
import pathlib
import paramiko

REPO = pathlib.Path(__file__).resolve().parent
PVE_HOST = "192.168.1.9"
PVE_USER = "root"
CT_ID = "200"
REMOTE_REPO = "/opt/itc-agents"

FILES = [
    ("shared/tools/memory.py", f"{REMOTE_REPO}/shared/tools/memory.py"),
    ("shared/tools/__init__.py", f"{REMOTE_REPO}/shared/tools/__init__.py"),
    ("deploy/requirements.txt", f"{REMOTE_REPO}/deploy/requirements.txt"),
]


def connect(host: str, user: str, password: str) -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, username=user, password=password, timeout=10, allow_agent=False, look_for_keys=False)
    return c


def run(c: paramiko.SSHClient, cmd: str, *, check: bool = True) -> tuple[int, str, str]:
    stdin, stdout, stderr = c.exec_command(cmd, timeout=120)
    rc = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    print(f"$ {cmd}\n  rc={rc}")
    if out.strip():
        print("  stdout:", out.strip()[:2000])
    if err.strip():
        print("  stderr:", err.strip()[:2000])
    if check and rc != 0:
        raise SystemExit(f"command failed: {cmd}")
    return rc, out, err


def main() -> int:
    pw = os.environ.get("PVE_ROOT_PASS") or getpass.getpass(f"Password for {PVE_USER}@{PVE_HOST}: ")
    print(f"[1/5] connecting to PVE {PVE_HOST}")
    pve = connect(PVE_HOST, PVE_USER, pw)

    # Verify CT 200 is running
    run(pve, f"pct status {CT_ID}")

    print(f"[2/5] uploading {len(FILES)} files to PVE then into CT {CT_ID}")
    sftp = pve.open_sftp()
    for local_rel, remote_dest in FILES:
        local = REPO / local_rel
        if not local.exists():
            raise SystemExit(f"missing local file: {local}")
        tmp = f"/tmp/itc-{local.name}"
        print(f"  -> {local} => PVE:{tmp}")
        sftp.put(str(local), tmp)
        # push into CT, after backing up the existing file if present
        run(pve, (
            f"pct exec {CT_ID} -- bash -lc '"
            f"if [ -f {remote_dest} ]; then cp -a {remote_dest} {remote_dest}.bak.$(date +%s); fi'"
        ), check=False)
        run(pve, f"pct push {CT_ID} {tmp} {remote_dest} --perms 0644")
        run(pve, f"rm -f {tmp}", check=False)
    sftp.close()

    print("[3/5] installing psycopg in CT venv")
    run(pve, (
        f"pct exec {CT_ID} -- bash -lc '"
        f"cd {REMOTE_REPO} && "
        f".venv/bin/pip install -q -r deploy/requirements.txt 2>&1 | tail -20'"
    ))

    print("[4/5] verifying registry import inside CT")
    run(pve, (
        f"pct exec {CT_ID} -- bash -lc '"
        f"cd {REMOTE_REPO} && "
        f".venv/bin/python -c \""
        f"from shared.tools import REGISTRY, builtins; "
        f"print(\\\"keys=\\\", len(REGISTRY)); "
        f"print(\\\"memory_store=\\\", \\\"memory_store\\\" in REGISTRY); "
        f"print(\\\"memory_retrieve=\\\", \\\"memory_retrieve\\\" in REGISTRY); "
        f"print(\\\"builtins=\\\", sorted(builtins()))\"'"
    ))

    print("[5/5] verifying agent_memory schema in Postgres")
    run(pve, (
        f"pct exec {CT_ID} -- bash -lc '"
        f"set -a; . /root/.itc-agents/secrets.env 2>/dev/null; set +a; "
        f"psql \"$DATABASE_URL\" -c \"\\d agent_memory\" 2>&1 | head -25'"
    ), check=False)

    print("\nDONE. Memory tools live in CT 200.")
    print("Optional next steps (not run automatically):")
    print("  - put OPENAI_API_KEY=sk-... into /root/.itc-agents/secrets.env to enable semantic mode")
    print("  - re-register agents:")
    print(f"    pct exec {CT_ID} -- bash -lc 'cd {REMOTE_REPO} && .venv/bin/python deploy/register-with-jarvis.py --jarvis http://127.0.0.1:5055'")
    pve.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
