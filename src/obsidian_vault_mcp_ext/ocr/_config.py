"""Configuration for the OCR extension (its own env-var namespace).

Off by default. The OCR programs are external commands, run without a shell: the template
is split into an argv list once and ``{path}`` is replaced by the resolved file path as a
single argument, so a file name can never become shell syntax or an extra option.
"""

import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


OCR_ENABLED = _env_bool("VAULT_OCR_ENABLED", False)

# Images: tesseract reads these directly.
OCR_IMAGE_EXTENSIONS = frozenset(
    ext.strip().lower()
    for ext in os.environ.get("VAULT_OCR_IMAGE_EXTENSIONS", ".png,.jpg,.jpeg,.webp,.tif,.tiff").split(",")
    if ext.strip()
)
OCR_IMAGE_CMD = os.environ.get("VAULT_OCR_IMAGE_CMD", "tesseract {path} - -l eng").strip()

# PDFs: tesseract cannot read a PDF, so this needs a command that renders pages first
# (e.g. pdftoppm piped into tesseract, or ocrmypdf --sidecar). Empty disables PDF OCR.
OCR_PDF_CMD = os.environ.get("VAULT_OCR_PDF_CMD", "").strip()
# Mixed PDFs: a scan with a few text pages (an e-signature trail, a typed cover sheet).
# With this on, PdfTextExtension asks for OCR of just the pages without text, listed in
# VAULT_PDF_OCR_PAGES; the command labels each page it handles with a form feed and
# "PAGE <n>" (or "PAGE <n> FAILED"). The production wrapper in the fork's docs/deploy
# does this. Off by default: it needs such a command, and it costs OCR time.
OCR_PDF_PARTIAL = _env_bool("VAULT_OCR_PDF_PARTIAL", False)

OCR_TIMEOUT_SECONDS = _env_int("VAULT_OCR_TIMEOUT", 120)
OCR_MAX_FILE_BYTES = _env_int("VAULT_OCR_MAX_FILE_BYTES", 50 * 1024 * 1024)

# Results are cached in memory by (path, size, mtime), so repeated reads of an unchanged
# file do not run OCR again. 0 disables the cache.
OCR_CACHE_ENTRIES = _env_int("VAULT_OCR_CACHE_ENTRIES", 256)

# Persist OCR text next to its source as <file>.ocr.txt, in the fork's format, so it
# survives a restart, is reused instead of re-running OCR, and existing fork-era
# sidecars keep working. Off only if explicitly disabled.
OCR_SIDECAR_ENABLED = _env_bool("VAULT_OCR_SIDECAR_ENABLED", True)
OCR_SIDECAR_SUFFIX = os.environ.get("VAULT_OCR_SIDECAR_SUFFIX", ".ocr.txt").strip() or ".ocr.txt"
