"""Entry point: run the host MCP server with this package's extensions loaded.

Compose only the extensions (and install only the extras) you want:

    serve([RecurringExtension(), SemanticExtension(), TemplatesExtension()])
"""

from obsidian_vault_mcp.server import serve

from .compat import CompatExtension
from .createnote import CreateNoteExtension
from .imports import ImportExtension
from .maintenance import MaintenanceExtension
from .ocr import OcrExtension
from .pdftext import PdfTextExtension
from .recurring import RecurringExtension
from .semantic import SemanticExtension
from .templates import TemplatesExtension


def main() -> None:
    # Default entry point loads all nine. Semantic fails soft without its [semantic] extra,
    # PDF text without [pdf]; import stays inert until VAULT_IMPORT_URL_ENABLED /
    # VAULT_IMPORT_FILE_ALLOWED_ROOTS, create-note until VAULT_CREATE_NOTE_PATH_PATTERN,
    # OCR until VAULT_OCR_ENABLED.
    # Order matters for the two content extractors: PdfTextExtension before OcrExtension,
    # so a PDF with a text layer never reaches OCR.
    # For a subset, write your own entry point and pass only the extensions you want.
    serve([
        TemplatesExtension(),
        SemanticExtension(),
        RecurringExtension(),
        ImportExtension(),
        MaintenanceExtension(),
        CompatExtension(),
        CreateNoteExtension(),
        PdfTextExtension(),
        OcrExtension(),
    ])


if __name__ == "__main__":
    main()
