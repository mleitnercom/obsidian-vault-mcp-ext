"""The content extractor the OCR extension registers with the host.

Signature is the host's ``register_content_extractor`` contract:
``extract(relative_path: str, path: Path) -> str | None``. The host calls it only from
the read tools and only for a file that is not valid UTF-8; ``path`` is already resolved
and past the host's containment and hardlink checks. Returning ``None`` declines, so the
host's own behaviour (an error for an unreadable file) applies.
"""

import logging
import shlex
import subprocess
import threading
from collections import OrderedDict
from pathlib import Path

from . import _config as config

logger = logging.getLogger(__name__)

_cache: "OrderedDict[tuple, str]" = OrderedDict()
_cache_lock = threading.Lock()


def _command_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in config.OCR_IMAGE_EXTENSIONS:
        return config.OCR_IMAGE_CMD
    if suffix == ".pdf":
        return config.OCR_PDF_CMD
    return ""


def build_argv(template: str, path: Path) -> list[str]:
    """Split the template without a shell and substitute ``{path}`` as one argument."""
    argv = shlex.split(template)
    if "{path}" not in argv:
        raise ValueError("OCR command template must contain {path} as its own argument")
    return [str(path) if part == "{path}" else part for part in argv]


def _run(argv: list[str], relative_path: str) -> str | None:
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            timeout=config.OCR_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("OCR timed out after %ss for %s", config.OCR_TIMEOUT_SECONDS, relative_path)
        return None
    except OSError as exc:
        logger.warning("OCR command could not start for %s: %s", relative_path, exc)
        return None
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        logger.warning("OCR failed for %s (exit %s): %s", relative_path, completed.returncode, stderr[:300])
        return None
    text = completed.stdout.decode("utf-8", errors="replace").strip()
    return text or None


def extract(relative_path: str, path: Path) -> str | None:
    """OCR an image or PDF the host cannot read; decline everything else."""
    template = _command_for(path)
    if not template:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    if stat.st_size > config.OCR_MAX_FILE_BYTES:
        logger.info("OCR skipped for %s: %s bytes exceeds the limit", relative_path, stat.st_size)
        return None

    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if config.OCR_CACHE_ENTRIES > 0:
        with _cache_lock:
            if key in _cache:
                _cache.move_to_end(key)
                return _cache[key]

    text = _run(build_argv(template, path), relative_path)
    if text is None:
        return None

    if config.OCR_CACHE_ENTRIES > 0:
        with _cache_lock:
            _cache[key] = text
            while len(_cache) > config.OCR_CACHE_ENTRIES:
                _cache.popitem(last=False)
    return text


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
