"""PdfTextExtension: PDF text layers through vault_read, before any OCR."""

import logging

from obsidian_vault_mcp.extensions import Extension

from . import extractor

logger = logging.getLogger(__name__)


class PdfTextExtension(Extension):
    """Registers a content extractor that returns a PDF's text layer (pypdf).

    Needs the optional ``[pdf]`` extra and a host with the content-extractor seam (#63,
    v0.3.0). Without either it logs once and does nothing. List it before OcrExtension:
    extractors are consulted in registration order, and a PDF with text should never be
    sent to OCR.
    """

    def __init__(self) -> None:
        self.registered = False

    def before_indexes_start(self, frontmatter_index) -> None:
        self.register()

    def register(self) -> bool:
        if self.registered:
            return True
        try:
            import pypdf  # noqa: F401
        except ImportError:
            logger.warning("PdfTextExtension: pypdf is not installed (pip install '.[pdf]'); PDFs stay unreadable")
            return False
        try:
            from obsidian_vault_mcp.content_extractors import register_content_extractor
        except ImportError:
            logger.warning("PdfTextExtension: host has no content-extractor seam; PDF text stays off")
            return False
        register_content_extractor(extractor.extract)
        self.registered = True
        return True
