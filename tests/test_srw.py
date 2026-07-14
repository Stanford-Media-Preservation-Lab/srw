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

import srw.cli as cli
from srw.cli import (
    Config,
    build_run_arg_parser,
    verify_md5,
    is_step_complete,
    get_step_completion_timestamp,
    mark_step_complete,
    DEFAULT_ATTACHMENT_SIZE,
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
