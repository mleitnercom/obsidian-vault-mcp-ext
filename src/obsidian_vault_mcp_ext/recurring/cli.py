"""Standalone CLI for recurring materialization (systemd-timer style).

Materializes pending periods once against the configured vault, prints the JSON
result on stdout, and returns an exit code. Provided as a callable ``cli_main``;
no console-script is wired (that is the host operator's choice). Unlike the fork's
CLI, there is no frontmatter-index bootstrap: idempotency and relative-mode
"last done" lookups are disk-based (see recurring/tools.py).
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import tools

logger = logging.getLogger(__name__)


def cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vault-recurring")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run recurring materialization once.")
    run.add_argument("--dry-run", action="store_true", help="Compute but do not write.")
    run.add_argument("--template-id", default=None, help="Limit to a single template id.")
    run.add_argument("--as-of", default=None, help="Override current date (YYYY-MM-DD).")

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    result = tools.recurring_materialize(
        dry_run=args.dry_run,
        template_id=args.template_id,
        as_of=args.as_of,
    )
    sys.stdout.write(result)
    sys.stdout.write("\n")
    return 0
