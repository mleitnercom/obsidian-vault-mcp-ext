"""Extensions for jimprosser/obsidian-web-mcp, loaded via its serve(extensions=...) seam.

Each feature is its own Extension subclass (not one monolith) so an operator loads only
what they want and installs only the dependencies that feature needs. Compose them:

    from obsidian_vault_mcp.server import serve
    from obsidian_vault_mcp_ext import TemplatesExtension, SemanticExtension, RecurringExtension
    serve([TemplatesExtension(), RecurringExtension()])   # e.g. without semantic

The classes are exposed lazily so that importing or using one extension never imports a
sibling (and never pulls a sibling's optional dependencies).
"""

__all__ = ["TemplatesExtension", "SemanticExtension", "RecurringExtension", "ImportExtension", "MaintenanceExtension"]


def __getattr__(name: str):
    if name == "TemplatesExtension":
        from .templates import TemplatesExtension
        return TemplatesExtension
    if name == "SemanticExtension":
        from .semantic import SemanticExtension
        return SemanticExtension
    if name == "RecurringExtension":
        from .recurring import RecurringExtension
        return RecurringExtension
    if name == "ImportExtension":
        from .imports import ImportExtension
        return ImportExtension
    if name == "MaintenanceExtension":
        from .maintenance import MaintenanceExtension
        return MaintenanceExtension
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
