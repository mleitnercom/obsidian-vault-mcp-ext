"""OCR sidecars: the OCR text persisted next to its source, in the fork's exact format.

A sidecar ``<file>.ocr.txt`` holds the OCR result with a header that identifies the
source it was made from:

    # OCR generated 2026-09-17T00:16:02Z
    # Source: 2026-05-19T08:12:44Z:3f2a9c0b1d4e5f60

    <text>

The source line is the file's modification time (seconds, UTC) and the first 16 hex
characters of the SHA-256 of its first 64 KB. A sidecar whose source line does not match
the current file is stale and ignored. The format is the fork's, byte for byte, so the
sidecars a fork-era server already wrote are reused as they are: switching to this
extension does not re-run OCR over the vault.

Writes are atomic through a dot-prefixed temp file (Obsidian Sync skips dotfiles), and an
exclusive lock file keeps two readers of the same image from running OCR twice.
"""

import hashlib
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from . import _config as config


def _iso_seconds(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fingerprint(path: Path) -> str:
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        digest.update(handle.read(64 * 1024))
    return f"{_iso_seconds(stat.st_mtime)}:{digest.hexdigest()[:16]}"


def sidecar_path(path: Path) -> Path:
    return path.with_name(f"{path.name}{config.OCR_SIDECAR_SUFFIX}")


def read_valid(path: Path) -> str | None:
    """The sidecar's text if it was made from the file as it is now, else None."""
    side = sidecar_path(path)
    if not side.is_file() or side.is_symlink():
        return None
    try:
        lines = side.read_text(encoding="utf-8").splitlines()
        expected = f"# Source: {fingerprint(path)}"
    except (OSError, UnicodeDecodeError):
        return None
    if len(lines) < 2 or lines[1].strip() != expected:
        return None
    text = "\n".join(lines[2:]).lstrip("\n")
    return text or None


def write(path: Path, text: str) -> tuple[Path, bool]:
    """Write the sidecar atomically; returns (sidecar path, created)."""
    side = sidecar_path(path)
    created = not side.exists()
    generated = datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload = f"# OCR generated {generated}\n# Source: {fingerprint(path)}\n\n{text.rstrip()}\n".encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=side.parent, prefix=f".{side.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.replace(tmp, side)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return side, created


class Lock:
    """Exclusive lock file next to the sidecar, so one image is OCR'd once."""

    def __init__(self, path: Path, timeout_seconds: float):
        self.lock_path = path.with_name(f".{path.name}{config.OCR_SIDECAR_SUFFIX}.lock")
        self.timeout = max(1.0, timeout_seconds)
        self.held = False

    def __enter__(self):
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                self.held = True
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    return self  # give up waiting; the caller re-checks the sidecar anyway
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, tb):
        if self.held:
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass
