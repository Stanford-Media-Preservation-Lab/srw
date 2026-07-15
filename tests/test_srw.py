"""
tests/test_srw.py

Unit tests for srw core logic. Covers pure functions and the log-based
resume-tracking helpers, which don't require rawcooked/mediaconch/ffmpeg
to be present. process_sequence() itself shells out to those tools and
is not covered here — validate it against a real or synthetic DPX
sequence with the tools installed.
"""

import datetime as datetime_module
import hashlib
import types

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
    DEFAULT_DISK_SPACE_MARGIN,
    TOTAL_STEPS,
    format_total_size,
    detect_dpx_frame_gaps,
    build_rawcooked_log_header,
    prepend_rawcooked_log_header,
    has_sufficient_disk_space,
    get_inventory_total_bytes,
    estimate_disk_space_margin,
    DISK_SPACE_HISTORICAL_PAD,
    preflight_check,
    tools_needed_for_range,
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
        assert args.start_step == 1
        assert args.end_step == TOTAL_STEPS
        assert args.skip_preflight is False
        assert args.disk_space_margin is None

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


# ---------------------------------------------------------------------------
# --help epilogs (full workflow reference, config format, docs pointers)
# ---------------------------------------------------------------------------

class TestHelpEpilogs:
    def test_top_level_epilog_mentions_docs_and_subcommands(self):
        parser = cli.build_arg_parser()
        assert parser.epilog is not None
        assert "MANUAL.md" in parser.epilog
        assert "srw run --help" in parser.epilog
        assert "srw batch --help" in parser.epilog

    def test_run_epilog_lists_all_steps(self):
        parser = cli.build_run_arg_parser()
        assert parser.epilog is not None
        assert "Pre-flight Check" in parser.epilog
        assert "Inventory Generation" in parser.epilog
        assert "MediaInfo Technical Metadata" in parser.epilog
        assert "Final Deliverable Hashes" in parser.epilog
        assert "--start-step 3 --end-step 4" in parser.epilog

    def test_run_subparser_via_top_level_also_gets_epilog(self):
        # add_run_arguments() is called both by build_run_arg_parser() (standalone)
        # and by build_arg_parser()'s "run" subparser -- confirm both paths set it.
        parser = cli.build_arg_parser()
        run_subparser = parser._subparsers._group_actions[0].choices["run"]
        assert run_subparser.epilog is not None
        assert "MediaInfo Technical Metadata" in run_subparser.epilog

    def test_batch_epilog_documents_config_format(self):
        parser = cli.build_arg_parser()
        batch_subparser = parser._subparsers._group_actions[0].choices["batch"]
        assert batch_subparser.epilog is not None
        assert "[[batch]]" in batch_subparser.epilog
        assert "batch_dir" in batch_subparser.epilog
        assert "max-parallel" in batch_subparser.epilog


# ---------------------------------------------------------------------------
# format_total_size (inventory summary line, MB -> GB switch)
# ---------------------------------------------------------------------------

class TestFormatTotalSize:
    def test_small_total_stays_in_mb(self):
        assert format_total_size(500 * 1024 * 1024) == "500.0 MB"

    def test_just_under_one_gib_stays_in_mb(self):
        assert format_total_size(1024 * 1024 * 1024 - 1) == "1024.0 MB"

    def test_one_gib_switches_to_gb(self):
        assert format_total_size(1024 * 1024 * 1024) == "1.0 GB"

    def test_large_total_reported_in_gb(self):
        # ~1050315.68 MB, the real figure from the user's report that motivated this
        total_bytes = round(1050315.68 * 1024 * 1024)
        result = format_total_size(total_bytes)
        assert result.endswith(" GB")
        assert result == "1025.7 GB"


# ---------------------------------------------------------------------------
# detect_dpx_frame_gaps
# ---------------------------------------------------------------------------

class TestDetectDpxFrameGaps:
    def test_no_gaps_in_contiguous_sequence(self):
        files = [f"/seq/frame_{i:05d}.dpx" for i in range(1, 6)]
        assert detect_dpx_frame_gaps(files) == []

    def test_detects_missing_frames(self):
        files = [f"/seq/frame_{i:05d}.dpx" for i in [1, 2, 4, 5, 8]]
        assert detect_dpx_frame_gaps(files) == [3, 6, 7]

    def test_empty_list_returns_no_gaps(self):
        assert detect_dpx_frame_gaps([]) == []

    def test_ignores_files_with_no_digits(self):
        assert detect_dpx_frame_gaps(["/seq/no_numbers_here.dpx"]) == []


# ---------------------------------------------------------------------------
# RAWcooked log header (build_rawcooked_log_header / prepend_rawcooked_log_header)
# ---------------------------------------------------------------------------

class TestRawcookedLogHeader:
    def test_header_contains_lab_name_and_formatted_timestamp(self):
        ts = datetime_module.datetime(2026, 7, 15, 15, 45)
        header = build_rawcooked_log_header(ts)
        assert "Stanford Media Preservation Lab" in header
        assert "RAWcooked processing completed 26/07/15, 03:45 PM" in header

    def test_prepend_adds_header_before_existing_content(self, tmp_path):
        log_path = tmp_path / "sequence.log"
        log_path.write_text("original rawcooked output\n")

        prepend_rawcooked_log_header(str(log_path))

        content = log_path.read_text()
        assert content.startswith("=" * 60)
        assert "Stanford Media Preservation Lab" in content
        assert content.rstrip().endswith("original rawcooked output")


# ---------------------------------------------------------------------------
# tools_needed_for_range (step-range-aware dependency checking)
# ---------------------------------------------------------------------------

class TestToolsNeededForRange:
    def test_full_range_needs_all_tools(self):
        tools = dict(tools_needed_for_range(1, TOTAL_STEPS))
        assert tools == {
            "mediaconch": "mediaconch",
            "rawcooked": "rawcooked",
            "mkvpropedit": "mkvtoolnix",
            "ffmpeg": "ffmpeg",
            "mediainfo": "mediainfo",
        }

    def test_mediaconch_only_range_needs_only_mediaconch(self):
        assert tools_needed_for_range(3, 4) == [("mediaconch", "mediaconch")]

    def test_pure_python_steps_need_no_tools(self):
        assert tools_needed_for_range(1, 2) == []
        assert tools_needed_for_range(10, 10) == []


# ---------------------------------------------------------------------------
# has_sufficient_disk_space
# ---------------------------------------------------------------------------

class TestHasSufficientDiskSpace:
    def test_enough_space_at_default_margin(self):
        assert has_sufficient_disk_space(total_source_bytes=100, available_bytes=115) is True

    def test_exactly_at_margin_boundary_passes(self):
        assert has_sufficient_disk_space(total_source_bytes=100, available_bytes=200, margin=2.0) is True

    def test_insufficient_space_fails(self):
        assert has_sufficient_disk_space(total_source_bytes=100, available_bytes=105, margin=1.1) is False

    def test_custom_margin_is_respected(self):
        assert has_sufficient_disk_space(total_source_bytes=100, available_bytes=100, margin=1.0) is True
        assert has_sufficient_disk_space(total_source_bytes=100, available_bytes=100, margin=1.5) is False


# ---------------------------------------------------------------------------
# get_inventory_total_bytes
# ---------------------------------------------------------------------------

class TestGetInventoryTotalBytes:
    def test_extracts_value_from_log(self, tmp_path):
        log_path = tmp_path / "seq1_process.log"
        log_path.write_text("[2026-07-15 00:00:00] >>> INVENTORY_TOTAL_BYTES: 123456\n")
        assert get_inventory_total_bytes(str(log_path)) == 123456

    def test_returns_none_when_marker_absent(self, tmp_path):
        log_path = tmp_path / "seq1_process.log"
        log_path.write_text("some other log content\n")
        assert get_inventory_total_bytes(str(log_path)) is None

    def test_returns_none_when_file_missing(self, tmp_path):
        assert get_inventory_total_bytes(str(tmp_path / "nope.log")) is None


# ---------------------------------------------------------------------------
# estimate_disk_space_margin
# ---------------------------------------------------------------------------

class TestEstimateDiskSpaceMargin:
    def test_returns_default_when_no_history(self, tmp_path):
        docs_dir = tmp_path / "Documents"
        docs_dir.mkdir()
        mkv_dir = tmp_path / "out"
        mkv_dir.mkdir()

        margin = estimate_disk_space_margin(str(docs_dir), str(mkv_dir), "seq_new")

        assert margin == DEFAULT_DISK_SPACE_MARGIN

    def test_estimates_from_prior_completed_sequence(self, tmp_path):
        docs_dir = tmp_path / "Documents"
        docs_dir.mkdir()
        mkv_dir = tmp_path / "out"
        mkv_dir.mkdir()

        # Prior sequence: 1,000,000 source bytes -> 400,000 byte mkv (0.4 ratio)
        (docs_dir / "priorseq_process.log").write_text(
            "[2026-07-01 00:00:00] >>> INVENTORY_TOTAL_BYTES: 1000000\n"
            "[2026-07-01 01:00:00] >>> SUCCESS: Step 10 completed successfully.\n"
        )
        (mkv_dir / "priorseq.mkv").write_bytes(b"x" * 400000)

        margin = estimate_disk_space_margin(str(docs_dir), str(mkv_dir), "seq_new")

        assert margin == pytest.approx(0.4 * DISK_SPACE_HISTORICAL_PAD)

    def test_excludes_the_current_sequence(self, tmp_path):
        docs_dir = tmp_path / "Documents"
        docs_dir.mkdir()
        mkv_dir = tmp_path / "out"
        mkv_dir.mkdir()
        (docs_dir / "seq_new_process.log").write_text(
            "[2026-07-01 00:00:00] >>> INVENTORY_TOTAL_BYTES: 1000000\n"
            "[2026-07-01 01:00:00] >>> SUCCESS: Step 10 completed successfully.\n"
        )
        (mkv_dir / "seq_new.mkv").write_bytes(b"x" * 400000)

        # Even a "completed" log for seq_new itself (e.g. left over from a
        # prior attempt at the same name) shouldn't be used to estimate its
        # own margin.
        margin = estimate_disk_space_margin(str(docs_dir), str(mkv_dir), "seq_new")

        assert margin == DEFAULT_DISK_SPACE_MARGIN

    def test_ignores_incomplete_sequences(self, tmp_path):
        docs_dir = tmp_path / "Documents"
        docs_dir.mkdir()
        mkv_dir = tmp_path / "out"
        mkv_dir.mkdir()
        # No Step 10 marker and no mkv written -- this sequence never finished.
        (docs_dir / "incomplete_process.log").write_text(
            "[2026-07-01 00:00:00] >>> INVENTORY_TOTAL_BYTES: 1000000\n"
        )

        margin = estimate_disk_space_margin(str(docs_dir), str(mkv_dir), "seq_new")

        assert margin == DEFAULT_DISK_SPACE_MARGIN

    def test_uses_worst_ratio_among_multiple_prior_sequences(self, tmp_path):
        docs_dir = tmp_path / "Documents"
        docs_dir.mkdir()
        mkv_dir = tmp_path / "out"
        mkv_dir.mkdir()

        # seq_a: ratio 0.3 (good compression, e.g. a clean B&W scan)
        (docs_dir / "seq_a_process.log").write_text(
            "[2026-07-01 00:00:00] >>> INVENTORY_TOTAL_BYTES: 1000000\n"
            "[2026-07-01 01:00:00] >>> SUCCESS: Step 10 completed successfully.\n"
        )
        (mkv_dir / "seq_a.mkv").write_bytes(b"x" * 300000)

        # seq_b: ratio 0.8 (worse compression, e.g. a grainy color negative)
        (docs_dir / "seq_b_process.log").write_text(
            "[2026-07-02 00:00:00] >>> INVENTORY_TOTAL_BYTES: 1000000\n"
            "[2026-07-02 01:00:00] >>> SUCCESS: Step 10 completed successfully.\n"
        )
        (mkv_dir / "seq_b.mkv").write_bytes(b"x" * 800000)

        # The worse (higher) ratio wins, since the margin is a required-space
        # floor and must cover the least-compressible case seen so far.
        margin = estimate_disk_space_margin(str(docs_dir), str(mkv_dir), "seq_new")

        assert margin == pytest.approx(0.8 * DISK_SPACE_HISTORICAL_PAD)


# ---------------------------------------------------------------------------
# preflight_check
# ---------------------------------------------------------------------------

class TestPreflightCheck:
    @pytest.fixture(autouse=True)
    def _set_log_path(self, tmp_path, monkeypatch):
        log_path = tmp_path / "sequence_process.log"
        monkeypatch.setattr(cli, "LOG_FILE_PATH", str(log_path))
        yield str(log_path)

    def _make_config(self, tmp_path, docs_dir, dpx_policy, wav_policy):
        out_dir = tmp_path / "out"
        out_dir.mkdir(exist_ok=True)
        return Config(
            source_parent=str(tmp_path / "Source"),
            mkv_out_dir=str(out_dir),
            docs_dir=str(docs_dir),
            mc_dir=str(tmp_path / "MediaConch"),
            dpx_policy=str(dpx_policy),
            wav_policy=str(wav_policy),
            attachment_size=DEFAULT_ATTACHMENT_SIZE,
        )

    def test_fails_when_metadata_xml_missing(self, tmp_path):
        source = tmp_path / "seq1"
        source.mkdir()
        docs_dir = tmp_path / "Documents"
        docs_dir.mkdir()
        config = self._make_config(tmp_path, docs_dir, tmp_path / "dpx_policy.xml", tmp_path / "wav_policy.xml")

        result = preflight_check(str(source), "seq1", config)

        assert result != True
        assert "Missing Required XML Metadata" in result

    def test_fails_when_dpx_policy_missing(self, tmp_path):
        source = tmp_path / "seq1"
        source.mkdir()
        docs_dir = tmp_path / "Documents"
        docs_dir.mkdir()
        (docs_dir / "seq1.xml").write_text("<Tags/>")
        config = self._make_config(tmp_path, docs_dir, tmp_path / "missing_policy.xml", tmp_path / "wav_policy.xml")

        result = preflight_check(str(source), "seq1", config)

        assert result != True
        assert "Missing DPX MediaConch Policy" in result

    def test_wav_policy_only_required_if_audio_present(self, tmp_path):
        source = tmp_path / "seq1"
        source.mkdir()
        (source / "frame1.dpx").write_bytes(b"x")
        (source / "frame1.dpx.md5").write_text("deadbeef  frame1.dpx\n")
        docs_dir = tmp_path / "Documents"
        docs_dir.mkdir()
        (docs_dir / "seq1.xml").write_text("<Tags/>")
        dpx_policy = tmp_path / "dpx_policy.xml"
        dpx_policy.write_text("<policy/>")
        config = self._make_config(tmp_path, docs_dir, dpx_policy, tmp_path / "missing_wav_policy.xml")

        # No .wav files in the source folder -- should pass even though the
        # configured WAV policy path doesn't exist.
        result = preflight_check(str(source), "seq1", config)

        assert result is True

    def test_relocated_sidecars_not_reported_as_missing(self, tmp_path):
        # Simulates a sequence that already completed Step 2: the sidecar has
        # been moved out of the source folder into the holding directory, which
        # is correct, not a missing-sidecar condition.
        source = tmp_path / "seq1"
        source.mkdir()
        (source / "frame1.dpx").write_bytes(b"x")
        docs_dir = tmp_path / "Documents"
        docs_dir.mkdir()
        (docs_dir / "seq1.xml").write_text("<Tags/>")
        dpx_policy = tmp_path / "dpx_policy.xml"
        dpx_policy.write_text("<policy/>")
        holding_dir = docs_dir / "seq1_source_md5_sidecars"
        holding_dir.mkdir()
        (holding_dir / "frame1.dpx.md5").write_text("deadbeef  frame1.dpx\n")
        config = self._make_config(tmp_path, docs_dir, dpx_policy, tmp_path / "wav_policy.xml")

        result = preflight_check(str(source), "seq1", config)

        assert result is True
        log_content = (tmp_path / "sequence_process.log").read_text()
        assert "missing a sidecar" not in log_content

    def test_fails_when_disk_space_insufficient(self, tmp_path, monkeypatch):
        source = tmp_path / "seq1"
        source.mkdir()
        (source / "frame1.dpx").write_bytes(b"x" * 1000)
        (source / "frame1.dpx.md5").write_text("deadbeef  frame1.dpx\n")
        docs_dir = tmp_path / "Documents"
        docs_dir.mkdir()
        (docs_dir / "seq1.xml").write_text("<Tags/>")
        dpx_policy = tmp_path / "dpx_policy.xml"
        dpx_policy.write_text("<policy/>")
        config = self._make_config(tmp_path, docs_dir, dpx_policy, tmp_path / "wav_policy.xml")

        monkeypatch.setattr(
            cli.shutil, "disk_usage",
            lambda path: types.SimpleNamespace(total=2000, used=1990, free=10),
        )

        result = preflight_check(str(source), "seq1", config)

        assert result != True
        assert "Insufficient Disk Space" in result

    def test_passes_when_disk_space_sufficient(self, tmp_path, monkeypatch):
        source = tmp_path / "seq1"
        source.mkdir()
        (source / "frame1.dpx").write_bytes(b"x" * 1000)
        (source / "frame1.dpx.md5").write_text("deadbeef  frame1.dpx\n")
        docs_dir = tmp_path / "Documents"
        docs_dir.mkdir()
        (docs_dir / "seq1.xml").write_text("<Tags/>")
        dpx_policy = tmp_path / "dpx_policy.xml"
        dpx_policy.write_text("<policy/>")
        config = self._make_config(tmp_path, docs_dir, dpx_policy, tmp_path / "wav_policy.xml")

        monkeypatch.setattr(
            cli.shutil, "disk_usage",
            lambda path: types.SimpleNamespace(total=10_000_000, used=0, free=10_000_000),
        )

        result = preflight_check(str(source), "seq1", config)

        assert result is True

    def test_disk_space_uses_estimate_from_prior_sequence(self, tmp_path, monkeypatch):
        source = tmp_path / "seq_new"
        source.mkdir()
        (source / "frame1.dpx").write_bytes(b"x" * 1000)
        (source / "frame1.dpx.md5").write_text("deadbeef  frame1.dpx\n")
        docs_dir = tmp_path / "Documents"
        docs_dir.mkdir()
        (docs_dir / "seq_new.xml").write_text("<Tags/>")
        dpx_policy = tmp_path / "dpx_policy.xml"
        dpx_policy.write_text("<policy/>")
        config = self._make_config(tmp_path, docs_dir, dpx_policy, tmp_path / "wav_policy.xml")

        # Prior sequence achieved a 0.3 ratio (good compression) -- with the
        # historical pad that requires far less than the flat 1.1x default
        # would for this source size.
        (docs_dir / "priorseq_process.log").write_text(
            "[2026-07-01 00:00:00] >>> INVENTORY_TOTAL_BYTES: 1000000\n"
            "[2026-07-01 01:00:00] >>> SUCCESS: Step 10 completed successfully.\n"
        )
        with open(os.path.join(config.mkv_out_dir, "priorseq.mkv"), "wb") as f:
            f.write(b"x" * 300000)

        monkeypatch.setattr(
            cli.shutil, "disk_usage",
            lambda path: types.SimpleNamespace(total=1000, used=650, free=350),
        )

        result = preflight_check(str(source), "seq_new", config)

        assert result is True
        log_content = (tmp_path / "sequence_process.log").read_text()
        assert "estimated from prior sequences" in log_content

    def test_passes_with_all_components_present(self, tmp_path):
        source = tmp_path / "seq1"
        source.mkdir()
        (source / "frame1.dpx").write_bytes(b"x")
        (source / "frame1.dpx.md5").write_text("deadbeef  frame1.dpx\n")
        docs_dir = tmp_path / "Documents"
        docs_dir.mkdir()
        (docs_dir / "seq1.xml").write_text("<Tags/>")
        dpx_policy = tmp_path / "dpx_policy.xml"
        dpx_policy.write_text("<policy/>")
        config = self._make_config(tmp_path, docs_dir, dpx_policy, tmp_path / "wav_policy.xml")

        result = preflight_check(str(source), "seq1", config)

        assert result is True
