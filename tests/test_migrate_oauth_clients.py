"""What a client registered with the fork can still do on the host after migration.

The fork persists client secrets only as hashes; the host imports only records with a
plain secret. The tests run the real host app (build_app) with a registry produced by the
migration and walk a connector's re-authorisation: authorize, log in, exchange the code,
present the token. Since per-client OAuth (upstream #108) the host checks a secret
whenever a client sends one, so a client that sends its original secret has to reconnect,
and the tool says which ones. The negative control loads the fork's file unconverted.
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

PUBLIC = "vault-mcp-4c3335e7543d1ba5"  # sends no secret, like ChatGPT
SECRET_POST = "vault-mcp-dc7693a3ef004526"  # sends its secret, like claude.ai
NO_METHOD = "vault-mcp-0000000000000000"  # field missing: RFC 7591 default, basic
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
FORK_STORE = {
    PUBLIC: {
        "client_secret_hash": hashlib.sha256(b"never-seen").hexdigest(),
        "redirect_uris": ["https://chatgpt.com/connector/oauth/abc"],
        "allow_client_credentials": False,
        "token_endpoint_auth_method": "none",
        "created_at": 1757000000.0,
    },
    SECRET_POST: {
        "client_secret_hash": hashlib.sha256(b"the-original-secret").hexdigest(),
        "redirect_uris": [REDIRECT],
        "allow_client_credentials": False,
        "token_endpoint_auth_method": "client_secret_post",
        "created_at": 1757000000.0,
    },
    NO_METHOD: {"client_secret_hash": "y" * 64, "redirect_uris": [REDIRECT], "allow_client_credentials": False},
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
    monkeypatch.setattr(host_config, "VAULT_OAUTH_CLIENT_ID", "")
    monkeypatch.setattr(host_config, "VAULT_MCP_PUBLIC_URL", "https://vault.example")

    def start(registry_file):
        monkeypatch.setattr(host_config, "OAUTH_CLIENTS_PATH", registry_file)
        oauth.close_oauth_state()
        oauth._auth_codes.clear()
        return TestClient(build_app(), base_url="https://vault.example")

    yield start
    oauth.close_oauth_state()
    oauth._auth_codes.clear()


def migrated(tmp_path):
    registry, _ = migrate.convert(FORK_STORE)
    target = tmp_path / "state" / "oauth_clients.json"
    migrate.write_registry(target, registry)
    return target, registry


def authorize_params(client_id):
    redirect = FORK_STORE[client_id]["redirect_uris"][0]
    return {"response_type": "code", "client_id": client_id, "redirect_uri": redirect, "state": "s",
            "code_challenge": CHALLENGE, "code_challenge_method": "S256"}


def code_for(client, client_id):
    params = authorize_params(client_id)
    form = client.get("/oauth/authorize", params=params)
    assert form.status_code == 200, form.text[:200]
    login = client.post("/oauth/authorize", data={**params, "username": "owner", "password": "correct-horse"},
                        follow_redirects=False)
    assert login.status_code == 302, login.text[:200]
    return login.headers["location"].split("code=")[1].split("&")[0]


def exchange(client, client_id, code, **extra):
    return client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": client_id,
        "redirect_uri": FORK_STORE[client_id]["redirect_uris"][0], "code_verifier": VERIFIER, **extra})


def auth_status(client, token):
    """The bearer middleware's verdict: 401 refused, 404 let through to a path with no route.

    Checked at the auth layer of build_app, not on the MCP transport: that needs the app
    lifespan, which the SDK's session manager runs only once per process.
    """
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.get("/no-such-route", headers=headers).status_code


def test_a_client_without_a_secret_reauthorises_without_reconnecting(host, tmp_path):
    target, _ = migrated(tmp_path)

    client = host(target)
    token = exchange(client, PUBLIC, code_for(client, PUBLIC))

    assert token.status_code == 200, token.text
    assert "refresh_token" in token.json()
    assert auth_status(client, None) == 401
    assert auth_status(client, token.json()["access_token"] + "x") == 401
    assert auth_status(client, token.json()["access_token"]) == 404


def test_a_client_that_sends_its_original_secret_must_reconnect(host, tmp_path):
    """What claude.ai sends after the switch: the secret the fork once issued."""
    target, registry = migrated(tmp_path)

    client = host(target)
    old = exchange(client, SECRET_POST, code_for(client, SECRET_POST), client_secret="the-original-secret")
    new = exchange(client, SECRET_POST, code_for(client, SECRET_POST),
                   client_secret=registry[SECRET_POST]["client_secret"])

    assert old.status_code == 401 and old.json()["error"] == "invalid_client", old.text
    assert new.status_code == 200, new.text


def test_the_tool_names_the_clients_that_must_reconnect(tmp_path, capsys):
    source = tmp_path / "fork.json"
    source.write_text(json.dumps(FORK_STORE), encoding="utf-8")

    assert migrate.main([str(source), str(tmp_path / "oauth_clients.json")]) == 0
    report = json.loads(capsys.readouterr().out)

    assert report["migrated"] == sorted([PUBLIC, SECRET_POST, NO_METHOD])
    assert report["reconnect_required"] == sorted([SECRET_POST, NO_METHOD])
    assert "vault-mcp-cc" in report["skipped"]


def test_the_fork_file_unconverted_loses_the_client(host, tmp_path):
    """Negative control: the host does not import hash-only records, so the connector is unknown."""
    raw = tmp_path / "state" / "oauth_clients.json"
    raw.parent.mkdir()
    raw.write_text(json.dumps(FORK_STORE), encoding="utf-8")

    response = host(raw).get("/oauth/authorize", params=authorize_params(PUBLIC))

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

    assert registry[PUBLIC]["client_secret"] != FORK_STORE[PUBLIC]["client_secret_hash"]
    assert len(registry[PUBLIC]["client_secret"]) == 64
