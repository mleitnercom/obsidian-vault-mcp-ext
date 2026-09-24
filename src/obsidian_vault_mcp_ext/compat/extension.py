"""CompatExtension: the fork's tool names, served by the host's own tools."""

from obsidian_vault_mcp.extensions import Extension

from . import tools

_RO = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
_WRITE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False}


class CompatExtension(Extension):
    """vault_str_replace, vault_patch, vault_batch_replace and vault_tree for clients and
    skills written against the fork. Text changes go through the host's vault_edit, so
    they carry every protection upstream applies to an edit; see compat/tools.py."""

    def register_tools(self, mcp) -> None:
        mcp.tool(
            name="vault_str_replace",
            description=(
                "Replace one exact string in a vault file. By default old_string must be unique; "
                "set replace_all=true to replace every occurrence."
            ),
            annotations=_WRITE,
        )(tools.vault_str_replace)
        mcp.tool(
            name="vault_patch",
            description=(
                "Replace one unique exact text occurrence in a file. Useful for targeted edits "
                "when a full rewrite would be overkill."
            ),
            annotations=_WRITE,
        )(tools.vault_patch)
        mcp.tool(
            name="vault_batch_replace",
            description=(
                "Replace exact strings across multiple files in one call. Each update "
                "({path, old_str, new_str, replace_all}) succeeds or fails on its own."
            ),
            annotations=_WRITE,
        )(tools.vault_batch_replace)
        mcp.tool(
            name="vault_tree",
            description="Return a nested JSON tree of the vault directory structure, up to depth 5.",
            annotations=_RO,
        )(tools.vault_tree)
