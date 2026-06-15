"""Entry point: run the host MCP server with this package's extensions loaded.

Compose only the extensions (and install only the extras) you want:

    serve([RecurringExtension(), SemanticExtension(), TemplatesExtension()])
"""

from obsidian_vault_mcp.server import serve

from .templates import TemplatesExtension


def main() -> None:
    serve([TemplatesExtension()])


if __name__ == "__main__":
    main()
