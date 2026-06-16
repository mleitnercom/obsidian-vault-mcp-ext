"""MaintenanceExtension: markdown encoding scan/repair + directory soft-delete as a seam extension."""

from obsidian_vault_mcp.extensions import Extension

from . import tools

_RO = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
_WRITE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False}


class MaintenanceExtension(Extension):
    """Vault housekeeping added through the host's extension seam without forking the host:
    detect markdown files that are not valid UTF-8, repair them by re-decoding from a chosen
    source encoding, and soft-delete directories into the vault's trash folder.

    Uses only upstream-public host APIs (resolve_vault_path / write_file_atomic and the host
    config's VAULT_PATH); the walk skips hidden/excluded directories and symlinks.
    """

    def register_tools(self, mcp) -> None:
        mcp.tool(
            name="vault_scan_encoding",
            description=(
                "Scan markdown files under the vault (or under path_prefix) and report those "
                "that are not valid UTF-8, with the byte position and reason. Read-only."
            ),
            annotations=_RO,
        )(tools.vault_scan_encoding)
        mcp.tool(
            name="vault_repair_encoding",
            description=(
                "Repair non-UTF-8 markdown files by re-decoding their bytes from source_encoding "
                "(default cp1252) and rewriting them as UTF-8 atomically. Use dry_run=true to "
                "preview without writing."
            ),
            annotations=_WRITE,
        )(tools.vault_repair_encoding)
        mcp.tool(
            name="vault_delete_directory",
            description=(
                "Soft-delete a directory by moving it into the vault's trash folder (timestamp "
                "suffix on collision). Refuses a non-empty directory unless only_if_empty=false."
            ),
            annotations=_WRITE,
        )(tools.vault_delete_directory)
