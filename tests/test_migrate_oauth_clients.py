"""A client registered with the fork completes the host's OAuth flow after migration.

The fork persists client secrets only as hashes; the host's loader drops records without
a plain secret. The test runs the real host app (build_app) with a registry produced by
the migration and walks a connector's re-authorisation: authorize, log in, exchange the
code. The negative control loads the fork's file unconverted and gets "Unknown client".
"""

import base64
import hashlib
import json

import pytest
from starlette.testclient import TestClient

from obsidian_vault_mcp import config as host_config
from obsidian_vault_mcp import oauth
from obsidian_vault_mcp.server import build_app

from obsidian_vault_mcp_ext import migrate_oauth_clients as migrate

CLIENT = "vault-mcp-4c3335e7543d1ba5"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
FORK_STORE = {
    CLIENT: {
        "client_secret_hash": hashlib.sha256(b"never-seen").hexdigest(),
        "redirect_uris": [REDIRECT],
        "allow_client_credentials": False,
        "token_endpoint_auth_method": "none",
        "created_at": 1757000000.0,
    },
    "vault-mcp-cc": {
        "client_secret_hash": "x" * 64, "redirect_uris": [REDIRECT],
        "allow_client_credentials": True, "token_endpoint_auth_method": "client_secret_post",
    },
}
VERIFIER = "v" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()


@pytest.fixture
def host(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(host_config, "VAULT_PATH", vault)
    monkeypatch.setattr(host_config, "VAULT_MCP_TOKEN", "the-static-bearer")
    monkeypatch.setattr(host_config, "VAULT_OAUTH_USERNAME", "owner")
    monkeypatch.setattr(host_config, "VAULT_OAUTH_PASSWORD", "correct-horse")
    monkeypatch.setattr(host_config, "VAULT_MCP_PUBLIC_URL", "https://vault.example")

    def start(registry_file):
        monkeypatch.setattr(host_config, "OAUTH_CLIENTS_PATH", registry_file)
        oauth._clients.clear()
        oauth._load_clients()
        return TestClient(build_app(), base_url="https://vault.example")

    yield start
    oauth._clients.clear()


def authorize_params():
    return {"response_type": "code", "client_id": CLIENT, "redirect_uri": REDIRECT, "state": "s",
            "code_challenge": CHALLENGE, "code_challenge_method": "S256"}


def test_a_migrated_client_reauthorises_without_reconnecting(host, tmp_path):
    registry, skipped = migrate.convert(FORK_STORE)
    target = tmp_path / "oauth_clients.json"
    migrate.write_registry(target, registry)
    client = host(target)

    form = client.get("/oauth/authorize", params=authorize_params())
    assert form.status_code == 200, form.text[:200]

    login = client.post("/oauth/authorize", data={**authorize_params(), "username": "owner", "password": "correct-horse"},
                        follow_redirects=False)
    assert login.status_code == 302, login.text[:200]
    code = login.headers["location"].split("code=")[1].split("&")[0]

    token = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": CLIENT,
                                               "redirect_uri": REDIRECT, "code_verifier": VERIFIER})
    assert token.status_code == 200, token.text
    assert token.json()["access_token"] == "the-static-bearer"
    assert "vault-mcp-cc" in skipped and "vault-mcp-cc" not in registry


def test_the_fork_file_unconverted_loses_the_client(host, tmp_path):
    """Negative control: the host drops hash-only records, so the connector is unknown."""
    raw = tmp_path / "oauth_clients.json"
    raw.write_text(json.dumps(FORK_STORE), encoding="utf-8")
    client = host(raw)

    response = client.get("/oauth/authorize", params=authorize_params())

    assert response.status_code == 400 and "Unknown client" in response.text


def test_an_existing_registry_is_not_replaced(tmp_path):
    source = tmp_path / "fork.json"
    source.write_text(json.dumps(FORK_STORE), encoding="utf-8")
    target = tmp_path / "oauth_clients.json"
    target.write_text("{}", encoding="utf-8")

    assert migrate.main([str(source), str(target)]) == 1
    assert target.read_text(encoding="utf-8") == "{}"


def test_fresh_secrets_are_not_the_hash(tmp_path):
    registry, _ = migrate.convert(FORK_STORE)

    assert registry[CLIENT]["client_secret"] != FORK_STORE[CLIENT]["client_secret_hash"]
    assert len(registry[CLIENT]["client_secret"]) == 64
