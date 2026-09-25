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
import re
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
    # Tuning for OCR wrappers (pages, DPI, languages). None of these is a secret. The page
    # list is set per call only: inherited, it would turn a whole-document run partial.
    env.update({k: v for k, v in os.environ.items()
                if k.startswith("VAULT_") and "OCR" in k and k != "VAULT_PDF_OCR_PAGES"})
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


_PAGE_BLOCK = re.compile(r"PAGE (\d+)( FAILED)?\n?(.*)", re.DOTALL)


def merge_labelled(per_page: list[str], pages: list[int], raw: str) -> tuple[str, list[int], list[int]] | None:
    """Put OCR text into the pages that had none, by label, never by position.

    Same contract as the fork (v0.15.1): a form feed and ``PAGE <n>`` per page handled,
    then its text; no text means blank; ``PAGE <n> FAILED`` means unreadable; a requested
    page without a label was capped. Text before the first label, an unlabelled block, a
    page not requested, one labelled twice, or no label at all rejects the output: a
    command that ignores the page list prints the whole document as one stream, and
    matching that by position would put a cover sheet's text on page 2.

    Returns (merged text, pages that got text, pages that failed).
    """
    head, *blocks = raw.split("\f")
    if head.strip() or not blocks:
        return None
    wanted = set(pages)
    texts = list(per_page)
    seen: set[int] = set()
    done: list[int] = []
    failed: list[int] = []
    for block in blocks:
        match = _PAGE_BLOCK.fullmatch(block)
        if match is None:
            return None
        number = int(match.group(1))
        if number not in wanted or number in seen:
            return None
        seen.add(number)
        if match.group(2):
            failed.append(number)
            continue
        text = match.group(3).strip()
        if text:
            texts[number - 1] = text
            done.append(number)
    return "\n\n".join(t for t in texts if t), sorted(done), sorted(failed)


def _run_pages(argv: list[str], path: Path, relative_path: str, pages: list[int]) -> str | None:
    env = child_env(path)
    env["VAULT_PDF_OCR_PAGES"] = ",".join(str(p) for p in pages)
    try:
        completed = subprocess.run(argv, capture_output=True, timeout=config.OCR_TIMEOUT_SECONDS, check=False, env=env)
    except subprocess.TimeoutExpired:
        logger.warning("Partial OCR timed out after %ss for %s", config.OCR_TIMEOUT_SECONDS, relative_path)
        return None
    except OSError as exc:
        logger.warning("Partial OCR could not start for %s: %s", relative_path, exc)
        return None
    if completed.returncode != 0:
        logger.warning("Partial OCR failed for %s (exit %s)", relative_path, completed.returncode)
        return None
    # Not stripped: the labels start with a form feed, which strip() would eat.
    return completed.stdout.decode("utf-8", errors="replace")


def extract_partial(relative_path: str, path: Path, per_page: list[str]) -> str | None:
    """OCR the pages of a mixed PDF that have no text; None leaves the text layer alone.

    Called by PdfTextExtension. A failed run, output that breaks the labels, or partial OCR
    being off all return None, and the caller serves the text layer as before.
    """
    if not (config.OCR_ENABLED and config.OCR_PDF_PARTIAL and config.OCR_PDF_CMD):
        return None
    pages = [n for n, text in enumerate(per_page, start=1) if not text]
    if not pages or len(pages) == len(per_page):
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    if stat.st_size > config.OCR_MAX_FILE_BYTES:
        return None

    key = ("partial", str(path), stat.st_size, stat.st_mtime_ns)
    if config.OCR_CACHE_ENTRIES > 0:
        with _cache_lock:
            if key in _cache:
                _cache.move_to_end(key)
                return _cache[key]

    def run() -> tuple[str, bool] | None:
        raw = _run_pages(build_argv(config.OCR_PDF_CMD, path), path, relative_path, pages)
        if raw is None:
            return None
        merged = merge_labelled(per_page, pages, raw)
        if merged is None:
            logger.warning("Partial OCR output for %s does not follow the page labels; ignored", relative_path)
            return None
        text, _done, failed = merged
        # A failed page is not a blank one: answer, but do not cache, so the next read retries.
        return text, not failed

    if config.OCR_SIDECAR_ENABLED:
        cached = sidecar.read_valid(path)
        if cached is not None:
            _remember(key, cached)
            return cached
        with sidecar.Lock(path, config.OCR_TIMEOUT_SECONDS * 2):
            cached = sidecar.read_valid(path)
            if cached is not None:
                _remember(key, cached)
                return cached
            result = run()
            if result is None:
                return None
            text, cacheable = result
            if cacheable:
                _store_sidecar(relative_path, path, text)
                _remember(key, text)
            return text

    result = run()
    if result is None:
        return None
    text, cacheable = result
    if cacheable:
        _remember(key, text)
    return text


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()
