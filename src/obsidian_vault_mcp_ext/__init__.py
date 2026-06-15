"""Extensions for jimprosser/obsidian-web-mcp, loaded via its serve(extensions=...) seam.

Each feature is its own Extension subclass (not one monolith) so an operator loads only
what they want and installs only the dependencies that feature needs. Compose them:

    from obsidian_vault_mcp.server import serve
    from obsidian_vault_mcp_ext import TemplatesExtension
    serve([TemplatesExtension()])
"""

from .templates_extension import TemplatesExtension

__all__ = ["TemplatesExtension"]
