"""The recurring scheduler in the server process, and the semantic reindex command line.

Scheduler: with a short interval the loop must actually materialize (control), keep
running after an iteration raises, stop on shutdown, and not start at all when the
interval is 0 or the feature is off.

CLI: it must refuse to pretend when semantic search is off (exit 2, no index), and report
status without building anything. The real rebuild needs the [semantic] extra and runs
where that is installed.
"""

import importlib
import json
import threading
import time

import pytest

from obsidian_vault_mcp import config as host_config

from obsidian_vault_mcp_ext.recurring import RecurringExtension
from obsidian_vault_mcp_ext.recurring import _config as recurring_config
from obsidian_vault_mcp_ext.recurring import tools as recurring_tools
from obsidian_vault_mcp_ext.semantic import _config as semantic_config
from obsidian_vault_mcp_ext.semantic import cli as semantic_cli


@pytest.fixture
def counted(monkeypatch):
    calls = []
    event = threading.Event()

    def fake_materialize(*args, **kwargs):
        calls.append(time.monotonic())
        if len(calls) == 2:
            raise RuntimeError("broken template")
        if len(calls) >= 3:
            event.set()
        return "{}"

    monkeypatch.setattr(recurring_tools, "recurring_materialize", fake_materialize)
    monkeypatch.setattr(recurring_config, "VAULT_RECURRING_ENABLED", True)
    return calls, event


def test_the_scheduler_runs_survives_a_failure_and_stops(counted, monkeypatch):
    calls, third_run = counted
    monkeypatch.setattr(recurring_config, "VAULT_RECURRING_INTERVAL", 0.05)
    ext = RecurringExtension()

    ext.after_indexes_start(None)
    assert third_run.wait(5), f"loop did not keep running after a failing iteration: {len(calls)} runs"
    ext.shutdown()
    stopped_at = len(calls)
    time.sleep(0.3)

    assert len(calls) >= 3
    assert len(calls) == stopped_at, "the loop kept running after shutdown"


def test_the_first_run_waits_one_interval(counted, monkeypatch):
    calls, _ = counted
    monkeypatch.setattr(recurring_config, "VAULT_RECURRING_INTERVAL", 30)
    ext = RecurringExtension()

    ext.after_indexes_start(None)
    time.sleep(0.2)
    ext.shutdown()

    assert calls == [], "materialized at startup instead of after one interval, as the fork does"


@pytest.mark.parametrize("enabled,interval", [(True, 0), (False, 60)])
def test_no_scheduler_without_interval_or_feature(counted, monkeypatch, enabled, interval):
    monkeypatch.setattr(recurring_config, "VAULT_RECURRING_ENABLED", enabled)
    monkeypatch.setattr(recurring_config, "VAULT_RECURRING_INTERVAL", interval)
    ext = RecurringExtension()

    ext.after_indexes_start(None)

    assert ext._thread is None
    ext.shutdown()


def test_starting_twice_does_not_start_two_loops(counted, monkeypatch):
    monkeypatch.setattr(recurring_config, "VAULT_RECURRING_INTERVAL", 60)
    ext = RecurringExtension()
    ext.after_indexes_start(None)
    first = ext._thread

    ext.after_indexes_start(None)

    assert ext._thread is first
    ext.shutdown()


# --- semantic CLI -------------------------------------------------------------------------

def test_cli_refuses_when_semantic_search_is_off(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(host_config, "VAULT_PATH", tmp_path)
    monkeypatch.setattr(semantic_config, "SEMANTIC_SEARCH_ENABLED", False)

    code = semantic_cli.cli_main(["reindex", "--mode", "full"])

    assert code == 2
    assert "OFF" in capsys.readouterr().out.upper()
    assert not (tmp_path / ".obsidian-vault-mcp").exists()


def test_cli_status_builds_nothing(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(host_config, "VAULT_PATH", tmp_path)
    monkeypatch.setattr(semantic_config, "SEMANTIC_SEARCH_ENABLED", True)

    code = semantic_cli.cli_main(["status"])

    assert code == 0
    status = json.loads(capsys.readouterr().out)
    assert status["enabled"] is True and status["chunk_count"] == 0


def _semantic_deps() -> bool:
    for mod in ("faiss", "fastembed", "rank_bm25", "numpy"):
        try:
            importlib.import_module(mod)
        except Exception:
            return False
    return True


@pytest.mark.skipif(not _semantic_deps(), reason="semantic extra not installed; proven on the server run")
def test_cli_full_reindex_builds_the_index(monkeypatch, capsys, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("# A\n\nSemantic retrieval with embeddings.\n", encoding="utf-8")
    monkeypatch.setattr(host_config, "VAULT_PATH", vault)
    monkeypatch.setattr(semantic_config, "SEMANTIC_SEARCH_ENABLED", True)

    code = semantic_cli.cli_main(["reindex", "--mode", "full"])

    result = json.loads(capsys.readouterr().out)
    assert code == 0, result
    assert result["indexed_files"] == 1 and result["indexed_chunks"] >= 1
