"""RecurringExtension: recurring-template materialization as a seam extension.

Exposes ``recurring_materialize`` as an MCP tool through the host's extension seam
without forking the host. The fork's optional in-process scheduler tied to the
server lifespan is intentionally OUT OF SCOPE here -- materialization is a tool;
the CLI lives in ``recurring/cli.py`` (no console-script wiring).
"""

from obsidian_vault_mcp.extensions import Extension

from . import tools

_WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}


class RecurringExtension(Extension):
    """Materialize ``type: recurring-template`` notes into concrete task instances.

    Strictly idempotent: a second run for the same template and period creates
    nothing new. Requires ``VAULT_RECURRING_TEMPLATES_FOLDER`` to be set; returns
    a capability error otherwise (fail-soft).
    """

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
