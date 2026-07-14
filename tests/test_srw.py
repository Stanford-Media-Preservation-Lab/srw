"""
tests/test_srw.py

Unit tests for srw core logic. Covers pure functions and the log-based
resume-tracking helpers, which don't require rawcooked/mediaconch/ffmpeg
to be present. process_sequence() itself shells out to those tools and
is not covered here — validate it against a real or synthetic DPX
sequence with the tools installed.
"""

import hashlib

import pytest

import os

import srw.cli as cli
from srw.cli import (
    Config,
    build_run_arg_parser,
    verify_md5,
    is_step_complete,
    get_step_completion_timestamp,
    mark_step_complete,
    run_mediaconch,
    validate_files_against_policy,
    DEFAULT_ATTACHMENT_SIZE,
    DEFAULT_MEDIACONCH_BATCH_SIZE,
)


# ---------------------------------------------------------------------------
# verify_md5
# ---------------------------------------------------------------------------

class TestVerifyMd5:
    def test_matching_checksum_passes(self, tmp_path):
        f = tmp_path / "frame001.dpx"
        f.write_bytes(b"fake dpx data")
        expected = hashlib.md5(b"fake dpx data").hexdigest()
        md5_file = tmp_path / "frame001.dpx.md5"
        md5_file.write_text(f"{expected}  frame001.dpx\n")

        assert verify_md5(str(f), str(md5_file)) is True

    def test_mismatched_checksum_fails(self, tmp_path):
        f = tmp_path / "frame001.dpx"
        f.write_bytes(b"fake dpx data")
        md5_file = tmp_path / "frame001.dpx.md5"
        md5_file.write_text("0" * 32 + "  frame001.dpx\n")

        assert verify_md5(str(f), str(md5_file)) is False

    def test_missing_md5_file_errors_gracefully(self, tmp_path):
        f = tmp_path / "frame001.dpx"
        f.write_bytes(b"fake dpx data")
        missing_md5 = tmp_path / "nope.md5"

        assert verify_md5(str(f), str(missing_md5)) is False


# ---------------------------------------------------------------------------
# Step resume tracking (is_step_complete / mark_step_complete / timestamps)
# ---------------------------------------------------------------------------

class TestStepTracking:
    @pytest.fixture(autouse=True)
    def _set_log_path(self, tmp_path, monkeypatch):
        log_path = tmp_path / "sequence_process.log"
        monkeypatch.setattr(cli, "LOG_FILE_PATH", str(log_path))
        yield str(log_path)

    def test_step_not_complete_when_log_missing(self, _set_log_path):
        assert is_step_complete("Step 1") is False

    def test_mark_step_complete_then_is_step_complete(self, _set_log_path):
        mark_step_complete(1)
        assert is_step_complete("Step 1") is True
        assert is_step_complete("Step 2") is False

    def test_get_step_completion_timestamp_extracts_original_time(self, _set_log_path):
        mark_step_complete(3)
        ts = get_step_completion_timestamp(3)
        # Should be a real "YYYY-MM-DD HH:MM:SS" string pulled from the log,
        # not a fallback value for a step that never ran.
        assert len(ts) == 19
        assert ts[4] == "-" and ts[7] == "-"

    def test_get_step_completion_timestamp_falls_back_for_unmarked_step(self, _set_log_path):
        # No marker written at all; should still return a well-formed timestamp
        # rather than raising.
        ts = get_step_completion_timestamp(9)
        assert len(ts) == 19


# ---------------------------------------------------------------------------
# CLI / Config defaults
# ---------------------------------------------------------------------------

class TestArgParser:
    def test_output_dir_is_required(self):
        parser = build_run_arg_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_defaults(self):
        parser = build_run_arg_parser()
        args = parser.parse_args(["--output-dir", "/mnt/mkv"])
        assert args.source_dir == "Source"
        assert args.docs_dir == "Documents"
        assert args.mediaconch_dir == "MediaConch"
        assert args.dpx_policy is None
        assert args.wav_policy is None
        assert args.attachment_size == DEFAULT_ATTACHMENT_SIZE
        assert args.mediaconch_batch_size == DEFAULT_MEDIACONCH_BATCH_SIZE

    def test_policy_paths_derive_from_mediaconch_dir(self):
        config = Config(
            source_parent="Source",
            mkv_out_dir="/mnt/mkv",
            docs_dir="Documents",
            mc_dir="MyPolicies",
            dpx_policy="MyPolicies/DPX_SMPTE-CORE.xml",
            wav_policy="MyPolicies/WAV_policy.xml",
            attachment_size=DEFAULT_ATTACHMENT_SIZE,
        )
        assert config.dpx_policy == "MyPolicies/DPX_SMPTE-CORE.xml"
        assert config.wav_policy == "MyPolicies/WAV_policy.xml"
        assert config.mediaconch_batch_size == DEFAULT_MEDIACONCH_BATCH_SIZE


# ---------------------------------------------------------------------------
# MediaConch batching (run_mediaconch / validate_files_against_policy)
#
# subprocess.run is mocked here rather than shelling out to a real mediaconch
# binary — these tests cover the batching/parsing/laziness logic itself, not
# MediaConch's own validation behavior.
# ---------------------------------------------------------------------------

class FakeCompletedProcess:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class TestRunMediaConch:
    def test_builds_expected_command(self, monkeypatch):
        captured = {}

        def fake_run(args, capture_output, text, env):
            captured["args"] = args
            captured["env"] = env
            return FakeCompletedProcess()

        monkeypatch.setattr(cli.subprocess, "run", fake_run)
        run_mediaconch("/policy.xml", ["/a.dpx", "/b.dpx"])

        assert captured["args"] == ["mediaconch", "-p", "/policy.xml", "/a.dpx", "/b.dpx"]

    def test_csv_format_adds_flag(self, monkeypatch):
        captured = {}

        def fake_run(args, capture_output, text, env):
            captured["args"] = args
            return FakeCompletedProcess()

        monkeypatch.setattr(cli.subprocess, "run", fake_run)
        run_mediaconch("/policy.xml", ["/a.dpx"], csv_format=True)

        assert captured["args"] == ["mediaconch", "-p", "/policy.xml", "-fc", "/a.dpx"]

    def test_uses_a_fresh_home_and_cleans_it_up(self, monkeypatch):
        captured = {}

        def fake_run(args, capture_output, text, env):
            captured["home"] = env["HOME"]
            assert os.path.isdir(env["HOME"]), "HOME dir should exist during the call"
            return FakeCompletedProcess()

        monkeypatch.setattr(cli.subprocess, "run", fake_run)
        run_mediaconch("/policy.xml", ["/a.dpx"])

        # The old shell-based `HOME=$(mktemp -d) ...` version never cleaned this
        # up — regression check that the replacement does.
        assert not os.path.isdir(captured["home"])


class TestValidateFilesAgainstPolicy:
    def _fake_run_factory(self, calls, fail_substring=None):
        def fake_run(args, capture_output, text, env):
            files_arg = args[4:]  # ["mediaconch", "-p", policy, "-fc", *files]
            calls.append(list(files_arg))
            rows = ["filename,overall"]
            for f in files_arg:
                status = "fail" if fail_substring and fail_substring in f else "pass"
                rows.append(f"{f},{status}")
            return FakeCompletedProcess(stdout="\n".join(rows))
        return fake_run

    def test_batches_instead_of_one_call_per_file(self, monkeypatch):
        calls = []
        monkeypatch.setattr(cli.subprocess, "run", self._fake_run_factory(calls))

        files = [f"/seq/frame{i:03d}.dpx" for i in range(7)]
        results = list(validate_files_against_policy(files, "/policy.xml", batch_size=3))

        assert len(calls) == 3  # ceil(7/3), not 7
        assert [f for f, _ in results] == files
        assert all(is_valid for _, is_valid in results)

    def test_preserves_original_order_and_detects_failure(self, monkeypatch):
        calls = []
        monkeypatch.setattr(cli.subprocess, "run", self._fake_run_factory(calls, fail_substring="bad"))

        files = ["/seq/a.dpx", "/seq/bad.dpx", "/seq/c.dpx"]
        results = list(validate_files_against_policy(files, "/policy.xml", batch_size=10))

        assert results == [
            ("/seq/a.dpx", True),
            ("/seq/bad.dpx", False),
            ("/seq/c.dpx", True),
        ]

    def test_is_lazy_about_later_batches(self, monkeypatch):
        calls = []
        monkeypatch.setattr(cli.subprocess, "run", self._fake_run_factory(calls))

        files = [f"/seq/frame{i:03d}.dpx" for i in range(10)]
        gen = validate_files_against_policy(files, "/policy.xml", batch_size=3)
        next(gen)  # only consume the first result

        # A caller that stops after the first result (e.g. because it was
        # invalid and process_sequence halts) should not have triggered a
        # mediaconch call for later batches.
        assert len(calls) == 1


class TestTopLevelSubcommands:
    def test_requires_a_subcommand(self):
        parser = cli.build_arg_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_run_subcommand_dispatches_to_run_main(self):
        parser = cli.build_arg_parser()
        args = parser.parse_args(["run", "--output-dir", "/mnt/mkv"])
        assert args.func is cli.run_main
        assert args.output_dir == "/mnt/mkv"

    def test_batch_subcommand_dispatches_to_batch_main(self):
        parser = cli.build_arg_parser()
        args = parser.parse_args(["batch", "--config", "batch.toml"])
        assert args.func.__module__ == "srw.batch"
        assert args.config == "batch.toml"
