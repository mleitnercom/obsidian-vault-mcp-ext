"""Carry the fork's registered OAuth clients over to the host's registry.

Why it is needed: the fork stores each dynamically registered client with only a SHA-256
hash of its secret (``client_secret_hash``); the host (obsidian-web-mcp) stores the
secret itself (``client_secret``) and, on load, drops every record without one. Copied
as is, the fork's file loads as an empty registry, and every connector that later
re-authorises replays a client_id the host does not know: "Unknown client", remove and
re-add the connector.

Why it works: the host checks a dynamic client's secret nowhere in the authorization
code flow; what it matches is client_id, redirect_uri and PKCE. So a record with the
same client_id and redirect URIs and a fresh random secret keeps the connector working.
The fresh secret is never handed to anyone; it only satisfies the loader.

What it refuses: a client registered for the client-credentials grant (the host only
accepts its configured VAULT_OAUTH_CLIENT_ID for that), a record without redirect URIs,
and an existing target file.

Usage::

    python -m obsidian_vault_mcp_ext.migrate_oauth_clients FORK_STORE HOST_STORE

The bearer token itself is VAULT_MCP_TOKEN on both sides; keep it unchanged and
already-connected clients keep working without re-authorising at all.
"""

import json
import os
import secrets
import sys
import time
from pathlib import Path


def convert(fork_store: dict) -> tuple[dict, dict]:
    """Return (host registry, {client_id: reason}) for the records that were skipped."""
    clients = fork_store.get("clients", fork_store) if isinstance(fork_store, dict) else {}
    converted, skipped = {}, {}
    for client_id, record in clients.items():
        if not isinstance(client_id, str) or not isinstance(record, dict):
            skipped[str(client_id)] = "not a client record"
            continue
        if record.get("allow_client_credentials"):
            skipped[client_id] = "client-credentials client; the host accepts only VAULT_OAUTH_CLIENT_ID for that grant"
            continue
        uris = record.get("redirect_uris")
        if not isinstance(uris, list) or not uris or not all(isinstance(u, str) for u in uris):
            skipped[client_id] = "no redirect URIs"
            continue
        converted[client_id] = {
            "client_secret": secrets.token_hex(32),
            "redirect_uris": list(uris),
            "created_at": float(record.get("created_at") or time.time()),
        }
    return converted, skipped


def write_registry(path: Path, registry: dict) -> None:
    """Write the host registry 0600, refusing to replace an existing file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(registry, f)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: python -m obsidian_vault_mcp_ext.migrate_oauth_clients FORK_STORE HOST_STORE", file=sys.stderr)
        return 2
    source, target = Path(args[0]), Path(args[1])
    registry, skipped = convert(json.loads(source.read_text(encoding="utf-8")))
    try:
        write_registry(target, registry)
    except FileExistsError:
        print(f"{target} exists; not replaced", file=sys.stderr)
        return 1
    print(json.dumps({"migrated": sorted(registry), "skipped": skipped}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
