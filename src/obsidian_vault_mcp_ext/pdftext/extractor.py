"""Text-layer extraction for PDFs, through the host's content-extractor seam.

Upstream reads no PDF at all: vault_read on one is a decode error. Most PDFs in a real
vault carry a text layer (in production 659 of 766), and reading it needs no OCR, only
pypdf. This extractor returns that text and declines everything else:

- a PDF without a text layer (a scan), so the next extractor, typically OCR, gets it,
- a PDF that needs a password to open (an owner-only password is fine),
- a file pypdf cannot parse,
- anything that is not a PDF.

Register it before the OCR extension, so a PDF with text never reaches OCR.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def extract(relative_path: str, path: Path) -> str | None:
    if path.suffix.lower() != ".pdf":
        return None
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - guarded at registration
        return None

    try:
        reader = PdfReader(str(path))
        if getattr(reader, "is_encrypted", False):
            # Many PDFs carry only an owner password (no editing or printing) and open
            # with an empty user password. decrypt("") returns 0 only when that fails;
            # is_encrypted stays True after a successful decrypt, because it describes
            # the file, so it must not be read as "still locked".
            if reader.decrypt("") == 0:
                logger.info("PDF text: %s needs a password, declining", relative_path)
                return None
        pages = []
        for page in reader.pages:
            try:
                text = (page.extract_text() or "").strip()
            except Exception:  # noqa: BLE001 - one broken page must not lose the rest
                text = ""
            if text:
                pages.append(text)
    except Exception as exc:  # noqa: BLE001 - a damaged PDF is a decline, not a crash
        logger.info("PDF text: could not read %s: %s", relative_path, exc)
        return None

    if not pages:
        return None
    return "\n\n".join(pages)
