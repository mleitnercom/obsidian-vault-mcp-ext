"""Client helpers for Obsidian Local REST API integration.

Ported from the fork's obsidian_rest.py; stdlib-only (no extra Python deps). Reads its
configuration from this package's own _config namespace.
"""

import socket
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from . import _config as config


@dataclass
class ObsidianRestError(Exception):
    """Structured Local REST API failure mapped to MCP-facing error codes."""

    error_code: str
    message: str
    status_code: int | None = None
    body: str = ""


def is_local_obsidian_rest_url(url: str | None = None) -> bool:
    """Return whether the configured Local REST URL targets loopback."""
    raw_url = (url if url is not None else config.VAULT_OBSIDIAN_REST_URL).strip()
    if not raw_url:
        return False
    host = (urlparse(raw_url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def _ssl_context():
    if config.VAULT_OBSIDIAN_REST_VERIFY_TLS:
        return None
    return ssl._create_unverified_context()


def _request_url(path: str) -> str:
    if not config.VAULT_OBSIDIAN_REST_URL:
        raise ObsidianRestError(
            "capability_unavailable",
            "VAULT_OBSIDIAN_REST_URL is not configured; Obsidian Local REST API features are disabled.",
        )
    return urljoin(f"{config.VAULT_OBSIDIAN_REST_URL}/", path.lstrip("/"))


def obsidian_rest_request(
    path: str,
    *,
    method: str = "GET",
    body: str | bytes | None = None,
    content_type: str | None = None,
    timeout: int | float | None = None,
) -> tuple[int, bytes]:
    """Perform one Local REST API request with consistent error mapping."""
    data = None
    if isinstance(body, str):
        data = body.encode("utf-8")
    elif body is not None:
        data = body

    headers = {}
    if config.VAULT_OBSIDIAN_REST_API_KEY:
        headers["Authorization"] = f"Bearer {config.VAULT_OBSIDIAN_REST_API_KEY}"
    if content_type:
        headers["Content-Type"] = content_type

    request = urllib.request.Request(_request_url(path), data=data, headers=headers, method=method)
    effective_timeout = timeout if timeout is not None else config.VAULT_OBSIDIAN_REST_TIMEOUT
    try:
        with urllib.request.urlopen(request, timeout=effective_timeout, context=_ssl_context()) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body_text = ""
        if exc.code == 401:
            raise ObsidianRestError("rest_auth_failed", "Obsidian Local REST API rejected the API key.", exc.code, body_text) from exc
        if exc.code == 404:
            raise ObsidianRestError("command_unknown", "Obsidian Local REST API endpoint or command was not found.", exc.code, body_text) from exc
        if exc.code == 400:
            raise ObsidianRestError("rest_bad_request", body_text or "Obsidian Local REST API rejected the request.", exc.code, body_text) from exc
        raise ObsidianRestError("plugin_misconfigured", body_text or str(exc), exc.code, body_text) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise ObsidianRestError("rest_timeout", "Obsidian Local REST API request timed out.") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise ObsidianRestError("rest_timeout", "Obsidian Local REST API request timed out.") from exc
        raise ObsidianRestError("plugin_unavailable", f"Obsidian Local REST API is not reachable: {reason}") from exc
    except ssl.SSLError as exc:
        raise ObsidianRestError("plugin_misconfigured", f"TLS error talking to Obsidian Local REST API: {exc}") from exc
