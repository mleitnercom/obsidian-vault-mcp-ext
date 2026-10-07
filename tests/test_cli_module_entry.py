"""``python -m <cli module>`` runs the CLI (found 2026-10-07).

semantic/cli.py and recurring/cli.py had no ``__main__`` block. ``python -m`` imported
them, did nothing and exited 0, which reads like a successful run. The console scripts
were unaffected; a timer or an operator calling the module form got a silent no-op.
"""

import json
import subprocess
import sys

import pytest

from test_end_to_end_http import child_env  # tests/ is on sys.path (rootdir, no package)


def run_module(module, args, tmp_path, extra=None):
    env = child_env(tmp_path, {"VAULT_PATH": str(tmp_path), "VAULT_MCP_TOKEN": "t", **(extra or {})})
    return subprocess.run([sys.executable, "-m", module, *args], env=env, capture_output=True, text=True, timeout=120)


def test_semantic_cli_module_runs(tmp_path):
    result = run_module("obsidian_vault_mcp_ext.semantic.cli", ["reindex", "--mode", "full"], tmp_path,
                        {"VAULT_SEMANTIC_SEARCH_ENABLED": "0"})

    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr[-500:])
    assert "VAULT_SEMANTIC_SEARCH_ENABLED is off" in json.loads(result.stdout)["error"]


@pytest.mark.parametrize("module", ["obsidian_vault_mcp_ext.semantic.cli", "obsidian_vault_mcp_ext.recurring.cli"])
def test_a_missing_command_is_an_error_not_a_silent_success(module, tmp_path):
    result = run_module(module, [], tmp_path)

    assert result.returncode == 2 and "usage:" in result.stderr, (result.returncode, result.stderr[-300:])
