"""tests/test_launcher.py -- the per-batch-folder launcher template (templates/run_srw.py)."""

import importlib.util
import os
import subprocess
import sys

import pytest

import srw.cli as cli

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER = os.path.join(REPO, "templates", "run_srw.py")


def load_launcher():
    spec = importlib.util.spec_from_file_location("run_srw_template", LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def restore_cli_state(monkeypatch):
    monkeypatch.setattr(cli, "SKIP_STEPS", [])
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setenv("PYTHONPATH", "")


def test_template_ships_with_empty_skip_list():
    assert load_launcher().SKIP_STEPS == []


def test_prepare_assigns_sorted_unique_skip_steps_to_cli():
    load_launcher()._prepare(REPO, [5, 3, 3], ["run"])
    assert cli.SKIP_STEPS == [3, 5]


def test_prepare_sets_pythonpath_for_child_processes():
    load_launcher()._prepare(REPO, [], ["run"])
    assert REPO in os.environ["PYTHONPATH"].split(os.pathsep)


@pytest.mark.parametrize("bad", [[0], [11], ["3"], [True], [2.5]])
def test_prepare_rejects_invalid_step_numbers(bad):
    with pytest.raises(SystemExit) as exc:
        load_launcher()._prepare(REPO, bad, ["run"])
    assert "1-10" in str(exc.value)


def test_prepare_refuses_batch_while_skipping():
    with pytest.raises(SystemExit) as exc:
        load_launcher()._prepare(REPO, [3], ["batch", "--config", "x.toml"])
    assert "batch" in str(exc.value)


def test_prepare_allows_batch_without_skips():
    load_launcher()._prepare(REPO, [], ["batch", "--config", "x.toml"])


def test_prepare_reports_missing_repo(tmp_path):
    with pytest.raises(SystemExit) as exc:
        load_launcher()._prepare(str(tmp_path), [], ["run"])
    assert "SRW_REPO" in str(exc.value)


def test_launcher_runs_from_an_unrelated_directory(tmp_path):
    script = tmp_path / "run_srw.py"
    text = open(LAUNCHER).read().replace('os.path.expanduser("~/srw")', repr(REPO))
    script.write_text(text)
    out = subprocess.run([sys.executable, str(script), "--version"], cwd=tmp_path,
                         capture_output=True, text=True, env={**os.environ, "PYTHONPATH": ""})
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == f"srw {cli.__version__}"
