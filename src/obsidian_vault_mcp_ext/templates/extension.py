"""TemplatesExtension: Templater-style rendering + Dataview DQL as a seam extension."""

from obsidian_vault_mcp.extensions import Extension

from . import tools

_RO = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
_WRITE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False}


class TemplatesExtension(Extension):
    """Simple ``{{token}}`` template rendering plus Dataview TABLE DQL via Obsidian Local
    REST API, added through the server's extension seam without forking the host.

    Fail-soft: with ``VAULT_OBSIDIAN_REST_URL`` unset the Dataview tool returns a capability
    error; the template tools work from vault files regardless.
    """

    def register_tools(self, mcp) -> None:
        mcp.tool(
            name="vault_template_list",
            description="List markdown templates under VAULT_TEMPLATER_FOLDER.",
            annotations=_RO,
        )(tools.vault_template_list)
        mcp.tool(
            name="vault_template_render",
            description=(
                "Render a template with simple {{token}} substitution (not full Templater); "
                "returns the rendered content without writing."
            ),
            annotations=_RO,
        )(tools.vault_template_render)
        mcp.tool(
            name="vault_template_apply",
            description=(
                "Render a simple template and write it to a target path (atomic). "
                "Set overwrite=true to replace an existing file."
            ),
            annotations=_WRITE,
        )(tools.vault_template_apply)
        mcp.tool(
            name="vault_dataview_query",
            description=(
                "Run a Dataview TABLE DQL query via Obsidian Local REST API. Requires "
                "VAULT_OBSIDIAN_REST_URL; returns a capability error when unset (fail-soft)."
            ),
            annotations=_RO,
        )(tools.vault_dataview_query)
