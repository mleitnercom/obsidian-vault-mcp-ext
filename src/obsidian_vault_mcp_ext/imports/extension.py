"""ImportExtension: SSRF-hardened URL import + allowlisted local-file import as a seam extension."""

from obsidian_vault_mcp.extensions import Extension

from . import tools

_WRITE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": True}


class ImportExtension(Extension):
    """Bring binary files into the vault by URL or local path, added through the host's
    extension seam without forking the host.

    URL import is the contested surface and is hardened accordingly: it connects only to the
    validated, pinned IP (no DNS-rebinding window), re-validates every redirect hop, denies
    non-public targets by default, and caps size. It is OFF until ``VAULT_IMPORT_URL_ENABLED``
    is set. Local-file import is a separate surface, disabled until
    ``VAULT_IMPORT_FILE_ALLOWED_ROOTS`` allowlists source roots.
    """

    def register_tools(self, mcp) -> None:
        mcp.tool(
            name="vault_import_url",
            description=(
                "Download an allowed binary file (image/PDF) from an http(s) URL into the vault. "
                "SSRF-hardened: pins the validated IP, re-validates redirects, denies non-public "
                "hosts by default, verifies media_type and optional expected_sha256. Disabled "
                "until VAULT_IMPORT_URL_ENABLED=true."
            ),
            annotations=_WRITE,
        )(tools.vault_import_url)
        mcp.tool(
            name="vault_import_file",
            description=(
                "Copy an allowed binary file from a local allowlisted source path into the vault. "
                "Disabled until VAULT_IMPORT_FILE_ALLOWED_ROOTS is configured."
            ),
            annotations={**_WRITE, "openWorldHint": False},
        )(tools.vault_import_file)
