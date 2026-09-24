"""Command line for the nightly semantic rebuild, the extension's counterpart of the fork's
``vault-semantic``.

Production rebuilds the semantic index nightly from a systemd timer
(``vault-semantic reindex --mode full``) and restarts the server afterwards so it loads
the fresh cache. With the index living in this package, the timer needs a command here:

    vault-mcp-ext-semantic reindex --mode full
    vault-mcp-ext-semantic status

It reads the same environment as the server (VAULT_PATH, VAULT_SEMANTIC_*), prints the
result as JSON on stdout and exits non-zero when the rebuild did not happen, so the timer's
failure shows up in systemd.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import _config as config


def cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vault-mcp-ext-semantic", description="Semantic index maintenance.")
    sub = parser.add_subparsers(dest="command", required=True)
    reindex = sub.add_parser("reindex", help="Rebuild the semantic cache.")
    reindex.add_argument("--mode", choices=("full", "incremental"), default="full")
    sub.add_parser("status", help="Show the engine status without building anything.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", stream=sys.stderr)

    if not config.SEMANTIC_SEARCH_ENABLED:
        sys.stdout.write(json.dumps({"error": "VAULT_SEMANTIC_SEARCH_ENABLED is off; nothing to do"}) + "\n")
        return 2

    from .engine import SemanticSearchEngine

    engine = SemanticSearchEngine()
    if args.command == "status":
        sys.stdout.write(json.dumps(engine.status) + "\n")
        return 0

    try:
        result = engine.reindex(full=args.mode == "full")
    except Exception as exc:  # noqa: BLE001 - reported, and the exit code says it failed
        logging.getLogger(__name__).error("Semantic reindex failed: %s", exc)
        sys.stdout.write(json.dumps({"error": str(exc)}) + "\n")
        return 1
    sys.stdout.write(json.dumps(result) + "\n")
    return 0


def main() -> None:  # console-script entry point
    raise SystemExit(cli_main())
