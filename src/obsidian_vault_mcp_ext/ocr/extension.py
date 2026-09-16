"""OcrExtension: OCR text for images and PDFs through the host's read tools."""

import logging

from obsidian_vault_mcp.extensions import Extension

from . import _config as config
from . import extractor

logger = logging.getLogger(__name__)


class OcrExtension(Extension):
    """Registers an OCR content extractor with the host.

    ``vault_read`` and ``vault_batch_read`` then return OCR text for a screenshot or a
    scanned PDF instead of failing on a file that is not UTF-8. No new tool: the client
    reads the file it already knows about. The host keeps extraction away from every tool
    that writes back, so an OCR result can never be saved over the original.

    Off until ``VAULT_OCR_ENABLED``. Needs a host that has the content-extractor seam; on
    an older host it logs once and does nothing.
    """

    def __init__(self) -> None:
        self.registered = False

    def before_indexes_start(self, frontmatter_index) -> None:
        self.register()

    def register(self) -> bool:
        """Register the extractor once. Returns whether it is registered."""
        if self.registered:
            return True
        if not config.OCR_ENABLED:
            logger.info("OcrExtension loaded but VAULT_OCR_ENABLED is off")
            return False
        try:
            from obsidian_vault_mcp.content_extractors import register_content_extractor
        except ImportError:
            logger.warning("OcrExtension: host has no content-extractor seam; OCR stays off")
            return False
        register_content_extractor(extractor.extract)
        self.registered = True
        logger.info("OcrExtension: OCR extractor registered")
        return True
