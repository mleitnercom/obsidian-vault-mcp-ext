"""RecurringExtension: recurring-template materialization as a seam extension.

Exposes ``recurring_materialize`` as an MCP tool and, when VAULT_RECURRING_INTERVAL is
set, runs it on that interval in the server process, the way the fork did: wait one
interval, materialize, repeat. A failing run is logged and the loop carries on; the
run-report and alert notes (VAULT_RECURRING_REPORT_PATH / _ALERT_PATH) make failures
visible in the vault. ``recurring/cli.py`` stays available for a systemd timer instead.
"""

import logging
import threading

from obsidian_vault_mcp.extensions import Extension

from . import _config as config
from . import tools

logger = logging.getLogger(__name__)

_WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}


class RecurringExtension(Extension):
    """Materialize ``type: recurring-template`` notes into concrete task instances.

    Strictly idempotent: a second run for the same template and period creates
    nothing new. Requires ``VAULT_RECURRING_TEMPLATES_FOLDER`` to be set; returns
    a capability error otherwise (fail-soft).
    """

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def register_tools(self, mcp) -> None:
        mcp.tool(
            name="recurring_materialize",
            description=(
                "Materialize pending recurring-template instances. Strictly idempotent: "
                "a second run for the same template and period creates nothing new. "
                "Args: dry_run (compute only), template_id (limit to one template), "
                "as_of (YYYY-MM-DD override of current date). Requires "
                "VAULT_RECURRING_TEMPLATES_FOLDER."
            ),
            annotations=_WRITE,
        )(tools.recurring_materialize)

    def after_indexes_start(self, frontmatter_index) -> None:
        interval = config.VAULT_RECURRING_INTERVAL
        if not config.VAULT_RECURRING_ENABLED or interval <= 0 or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, args=(interval,), name="recurring-scheduler", daemon=True)
        self._thread.start()
        logger.info("Recurring scheduler started (every %ss)", interval)

    def _loop(self, interval: float) -> None:
        while not self._stop.wait(interval):
            try:
                tools.recurring_materialize()
            except Exception:  # noqa: BLE001 - a broken template must not end the loop
                logger.exception("Recurring scheduler iteration failed")

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None
