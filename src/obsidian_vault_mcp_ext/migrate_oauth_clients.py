"""Carry the fork's registered OAuth clients over to the host's registry.

Why it is needed: the fork stores each dynamically registered client with only a SHA-256
hash of its secret (``client_secret_hash``); the host (obsidian-web-mcp) stores the
secret itself (``client_secret``) and, on load, drops every record without one. Copied
as is, the fork's file loads as an empty registry, and every connector that later
re-authorises replays a client_id the host does not know: "Unknown client", remove and
re-add the connector.

What carries over, and what does not: the host imports the registry into its own OAuth
store on start and matches client_id, redirect_uri and PKCE. A client that sends no
secret (``token_endpoint_auth_method: none``, e.g. ChatGPT) re-authorises as before. A
client that sends one is checked against the stored secret since per-client OAuth
(upstream #108), and the fork kept only a hash of the original. The record therefore
gets a fresh random secret the client does not know, and its next token request fails
with ``invalid_client``: that connector has to be removed and added once. The tool
lists such clients under ``reconnect_required``, counting every method other than
``none``, since each of them authenticates with a secret the host does not have.

What it refuses: a client registered for the client-credentials grant (the host only
accepts its configured VAULT_OAUTH_CLIENT_ID for that), a record without redirect URIs,
and an existing target file.

Usage::

    python -m obsidian_vault_mcp_ext.migrate_oauth_clients FORK_STORE HOST_STORE

The host still accepts the static VAULT_MCP_TOKEN as a bearer; keep it unchanged and a
connected client keeps working until it re-authorises or refreshes its token.
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


def reconnect_required(fork_store: dict, registry: dict) -> list[str]:
    """The migrated clients that authenticate with a secret, so must reconnect once.

    A record without the field counts as ``client_secret_basic``, the RFC 7591 default.
    """
    clients = fork_store.get("clients", fork_store) if isinstance(fork_store, dict) else {}
    return sorted(
        client_id for client_id in registry
        if clients[client_id].get("token_endpoint_auth_method", "client_secret_basic") != "none"
    )


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
    fork_store = json.loads(source.read_text(encoding="utf-8"))
    registry, skipped = convert(fork_store)
    try:
        write_registry(target, registry)
    except FileExistsError:
        print(f"{target} exists; not replaced", file=sys.stderr)
        return 1
    print(json.dumps({"migrated": sorted(registry), "skipped": skipped,
                      "reconnect_required": reconnect_required(fork_store, registry)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
