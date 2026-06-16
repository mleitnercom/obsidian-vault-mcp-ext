"""Entry point: run the host MCP server with this package's extensions loaded.

Compose only the extensions (and install only the extras) you want:

    serve([RecurringExtension(), SemanticExtension(), TemplatesExtension()])
"""

from obsidian_vault_mcp.server import serve

from .imports import ImportExtension
from .maintenance import MaintenanceExtension
from .recurring import RecurringExtension
from .semantic import SemanticExtension
from .templates import TemplatesExtension


def main() -> None:
    # Default entry point loads all five; semantic fails soft without its [semantic] extra,
    # and import stays inert until VAULT_IMPORT_URL_ENABLED / VAULT_IMPORT_FILE_ALLOWED_ROOTS.
    # For a subset, write your own entry point and pass only the extensions you want.
    serve([TemplatesExtension(), SemanticExtension(), RecurringExtension(), ImportExtension(), MaintenanceExtension()])


if __name__ == "__main__":
    main()
