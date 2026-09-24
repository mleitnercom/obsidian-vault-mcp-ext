"""The content extractor the OCR extension registers with the host.

Signature is the host's ``register_content_extractor`` contract:
``extract(relative_path: str, path: Path) -> str | None``. The host calls it only from
the read tools and only for a file that is not valid UTF-8; ``path`` is already resolved
and checked by the host. Returning ``None`` declines, so the host's own behaviour (an
error for an unreadable file) applies.

Order of lookups: the in-memory cache, then a valid sidecar next to the file (see
sidecar.py), then the OCR command, whose result is written as a sidecar.

The OCR command runs without a shell and with a scrubbed environment: only what a
program needs to start and the OCR tuning variables (VAULT_*OCR*) are passed. The server
process holds VAULT_MCP_TOKEN, OAuth and upload secrets; an external program has no
business seeing them.
"""

import logging
import os
import shlex
import subprocess
import threading
from collections import OrderedDict
from pathlib import Path

from .._mutations import mutation
from . import _config as config
from . import sidecar

logger = logging.getLogger(__name__)

_cache: "OrderedDict[tuple, str]" = OrderedDict()
_cache_lock = threading.Lock()

# What an OCR program needs to start and find its data. Nothing that authenticates.
_ENV_PASSTHROUGH = (
    "PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP", "TMP",
    "SYSTEMROOT", "COMSPEC", "PATHEXT", "TESSDATA_PREFIX", "OMP_THREAD_LIMIT",
)


def child_env(path: Path) -> dict:
    """The OCR command's whole environment. Built here, never inherited."""
    env = {name: os.environ[name] for name in _ENV_PASSTHROUGH if name in os.environ}
    # Tuning for OCR wrappers (pages, DPI, languages). None of these is a secret.
    env.update({k: v for k, v in os.environ.items() if k.startswith("VAULT_") and "OCR" in k})
    # The fork's PDF wrapper also reads the file from here.
    env["VAULT_PDF_PATH"] = str(path)
    return env


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


def _run(argv: list[str], path: Path, relative_path: str) -> str | None:
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            timeout=config.OCR_TIMEOUT_SECONDS,
            check=False,
            env=child_env(path),
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


def _remember(key: tuple, text: str) -> None:
    if config.OCR_CACHE_ENTRIES > 0:
        with _cache_lock:
            _cache[key] = text
            while len(_cache) > config.OCR_CACHE_ENTRIES:
                _cache.popitem(last=False)


def _store_sidecar(relative_path: str, path: Path, text: str) -> None:
    rel_side = f"{relative_path}{config.OCR_SIDECAR_SUFFIX}"
    try:
        with mutation("ocr_sidecar", rel_side) as m:
            _side, created = sidecar.write(path, text)
            m.created = created
    except OSError as exc:
        # The text is still returned; only the persistence failed.
        logger.warning("Could not write OCR sidecar for %s: %s", relative_path, exc)


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

    if config.OCR_SIDECAR_ENABLED:
        cached = sidecar.read_valid(path)
        if cached is not None:
            _remember(key, cached)
            return cached
        with sidecar.Lock(path, config.OCR_TIMEOUT_SECONDS * 2):
            # Another reader may have written it while this one waited.
            cached = sidecar.read_valid(path)
            if cached is not None:
                _remember(key, cached)
                return cached
            text = _run(build_argv(template, path), path, relative_path)
            if text is None:
                return None
            _store_sidecar(relative_path, path, text)
            _remember(key, text)
            return text

    text = _run(build_argv(template, path), path, relative_path)
    if text is None:
        return None
    _remember(key, text)
    return text


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
