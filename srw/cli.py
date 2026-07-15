"""
srw.cli — core logic for the SMPL RAWcooked Workflow tool.

Stanford Media Preservation Lab
DPX --> FFv1/MKV batch preservation transcoding, with MD5 verification,
MediaConch policy validation, and a resumable ten-step per-sequence log.
"""

import os
import csv
import hashlib
import re
import subprocess
import datetime
import glob
import shutil
import sys
import argparse
import tempfile
import io
from dataclasses import dataclass
from typing import Optional

__version__ = "1.4.1"

DEFAULT_ATTACHMENT_SIZE = 5_000_000  # 5 MB; RAWcooked's own default is 1 MB

# DPX sequences routinely run to tens or hundreds of thousands of frames.
# MediaConch accepts a file list in one invocation (`mediaconch -p policy f1 f2 ...`),
# so files are validated in batches rather than one subprocess per file — but the
# full list still has to fit under the OS's ARG_MAX for a single exec, so it's
# chunked. 2000 paths comfortably fits (even at ~150 bytes/path that's ~300KB,
# well under the ~1-2MB ARG_MAX on Linux/macOS) while still cutting subprocess
# count by ~2000x for a 100k-frame sequence.
DEFAULT_MEDIACONCH_BATCH_SIZE = 2000

# FFv1 lossless encoding isn't guaranteed to shrink DPX-sized data by any
# particular ratio -- a clean B&W scan can end up well under 50% of its DPX
# size, while a grainy color negative might barely compress at all. Rather
# than guessing one fixed ratio for all content, the pre-flight disk-space
# check estimates it from this deployment's own prior completed sequences
# (see estimate_disk_space_margin()) and only falls back to this constant --
# an assumed ~1:1 MKV size plus a 10% pad for the review derivative,
# RAWcooked log, and manifest -- for the very first sequence in a fresh
# deployment, before any real compression data exists yet to learn from.
DEFAULT_DISK_SPACE_MARGIN = 1.1

# Extra cushion applied on top of the worst (least-compressed) ratio actually
# observed among this deployment's prior completed sequences -- smaller than
# DEFAULT_DISK_SPACE_MARGIN's built-in pad since it's backing a real
# measurement rather than a total guess.
DISK_SPACE_HISTORICAL_PAD = 1.05

# Marker written to a sequence's process log by Step 1, recording its total
# source (DPX+WAV) byte count so estimate_disk_space_margin() can look it up
# later and pair it with that sequence's actual finished MKV size.
INVENTORY_TOTAL_BYTES_MARKER = "INVENTORY_TOTAL_BYTES"

TOTAL_STEPS = 10

STEP_NAMES = {
    1: "INVENTORY GENERATION",
    2: "CHECKSUM VERIFICATION",
    3: "MEDIACONCH DPX VALIDATION",
    4: "MEDIACONCH WAV VALIDATION",
    5: "MANIFEST GENERATION (.md5)",
    6: "RAWCOOKED TRANSCODE",
    7: "EMBED METADATA TAGS",
    8: "GENERATE REVIEW DERIVATIVE",
    9: "MEDIAINFO TECHNICAL METADATA",
    10: "FINAL DELIVERABLE HASHES",
}

# Which external tool each step shells out to, and the apt package that
# provides it. Steps not listed here (1, 2, 5, 10) are pure Python (os.walk,
# hashlib) and need nothing installed. Keyed by step number so a narrow
# --start-step/--end-step range only demands the tools it will actually call.
STEP_DEPENDENCIES = {
    3: ("mediaconch", "mediaconch"),
    4: ("mediaconch", "mediaconch"),
    6: ("rawcooked", "rawcooked"),
    7: ("mkvpropedit", "mkvtoolnix"),
    8: ("ffmpeg", "ffmpeg"),
    9: ("mediainfo", "mediainfo"),
}

# Title-case name + one-line description for each step, used only to build
# `srw run --help`'s epilog -- kept separate from STEP_NAMES (the all-caps
# banner text written into process logs, which must stay stable for the
# resume system) so reformatting this table can never affect log output.
_STEP_HELP_ROWS = [
    ("0", "Pre-flight Check", "audio Y/N, DPX frame-gap scan, sidecar coverage (warn only);"),
    ("", "", "halts on missing metadata XML, MediaConch policy, or disk space"),
    ("1", "Inventory Generation", "{docs-dir}/{sequence}_inventory.csv"),
    ("2", "Checksum Verification", "verifies sidecar .md5s, relocates them out of the source folder"),
    ("3", "MediaConch DPX Validation", "validates every .dpx against your DPX policy"),
    ("4", "MediaConch WAV Validation", "validates any .wav against your WAV policy (skipped if none)"),
    ("5", "Manifest Generation", "{source}/{sequence}/{sequence}.md5 (embedded into the MKV)"),
    ("6", "RAWcooked Transcode", "{output-dir}/{sequence}.mkv + .log"),
    ("7", "Embed Metadata Tags", "tags MKV from {docs-dir}/{sequence}.xml via mkvpropedit"),
    ("8", "Generate Review Derivative", "{output-dir}/{sequence}_rawcooked_review.mp4"),
    ("9", "MediaInfo Technical Metadata", "{docs-dir}/{sequence}_mediainfo.txt (mediainfo -f -i)"),
    ("10", "Final Deliverable Hashes", ".mkv.md5 and .log.md5"),
]


def _format_step_table():
    return "\n".join(
        f"  {num:>2}  {name:<30} {detail}" if name else f"  {num:>2}  {'':<30} {detail}"
        for num, name, detail in _STEP_HELP_ROWS
    )


RUN_EPILOG = f"""\
Workflow (Step 0 pre-flight check, then Steps 1-10; every step writes a
resumable success marker to the process log, so a failed or interrupted
run can simply be re-run):

{_format_step_table()}

Examples:
  srw run --output-dir /media/smpl-5220r/A/MKV
  srw run --output-dir /media/smpl-5220r/A/MKV --start-step 3 --end-step 4
  srw run --output-dir /media/smpl-5220r/A/MKV --disk-space-margin 0.6

Required external tools: rawcooked, mediaconch, mkvtoolnix (mkvpropedit),
ffmpeg, mediainfo -- see INSTALL_UBUNTU.md. Only the tools needed by the
steps within --start-step/--end-step are checked.

Full reference: MANUAL.md in https://github.com/michaelangeletti/srw
"""

TOP_LEVEL_EPILOG = """\
Takes folders of DPX image sequences (with optional WAV audio) from film
scanning and packages them into lossless FFv1/Matroska (MKV) files for
long-term preservation in an OAIS digital repository, with MD5 verification,
MediaConch policy validation, embedded metadata tagging, an H.264 review
derivative, and a MediaInfo technical metadata dump -- all tracked through a
resumable, timestamped per-sequence log. Runs on Ubuntu 24.04.

Commands:
  run     Process one set of Source/Documents/MediaConch directories.
          See `srw run --help` for the full ten-step workflow reference.
  batch   Launch multiple `srw run` invocations in parallel from a TOML config.
          See `srw batch --help` for the config format.

Documentation: https://github.com/michaelangeletti/srw
  README.md          -- quick start and workflow overview
  MANUAL.md          -- full step-by-step reference, resume system, design decisions
  INSTALL_UBUNTU.md  -- dependency and pipx install instructions
"""


@dataclass
class Config:
    source_parent: str
    mkv_out_dir: str
    docs_dir: str
    mc_dir: str
    dpx_policy: str
    wav_policy: str
    attachment_size: int
    mediaconch_batch_size: int = DEFAULT_MEDIACONCH_BATCH_SIZE
    start_step: int = 1
    end_step: int = TOTAL_STEPS
    skip_preflight: bool = False
    # None means "estimate from this deployment's own prior sequences" (see
    # estimate_disk_space_margin()); an explicit value always overrides that.
    disk_space_margin: Optional[float] = None


# Set per-sequence by process_sequence(); read by the step-tracking helpers below.
LOG_FILE_PATH = ""


def write_log(message, print_to_screen=True):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] {message}"
    if print_to_screen:
        print(log_entry)
    if LOG_FILE_PATH:
        with open(LOG_FILE_PATH, "a") as f:
            f.write(log_entry + "\n")


def is_step_complete(step_marker):
    """Checks the log file to see if a specific step was already successful."""
    if not os.path.exists(LOG_FILE_PATH):
        return False
    search_string = f">>> SUCCESS: {step_marker} completed successfully."
    try:
        with open(LOG_FILE_PATH, "r") as f:
            log_content = f.read()
        return search_string in log_content
    except Exception:
        return False


def get_step_completion_timestamp(step_num):
    """Retrieves the original timestamp for a completed step from the log file."""
    search_string = f">>> SUCCESS: Step {step_num} completed successfully."
    try:
        with open(LOG_FILE_PATH, "r") as f:
            for line in f:
                if search_string in line:
                    if line.startswith("[") and "]" in line:
                        return line[1:line.index("]")]
    except Exception:
        pass
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def mark_step_complete(step_num):
    """Explicitly marks a step as complete in the log."""
    marker = f"Step {step_num}"
    write_log(f"\n>>> SUCCESS: {marker} completed successfully.")


def write_banner(title):
    divider = "=" * 60
    write_log(f"\n{divider}")
    write_log(f" STEP: {title}")
    write_log(f"{divider}\n")


def init_log_header():
    if not os.path.exists(LOG_FILE_PATH):
        header = (
            "============================================================\n"
            "Stanford Media Preservation Lab\n"
            f"srw — SMPL RAWcooked Workflow, v{__version__} (Resumable)\n"
            "DPX --> FFv1/mkv\n"
            "============================================================\n\n"
        )
        with open(LOG_FILE_PATH, "w") as f:
            f.write(header)
    else:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        resume_note = (
            "\n\n"
            "============================================================\n"
            f"SCRIPT RESUMED: {timestamp}\n"
            "Continuing from previous attempt after error/interruption\n"
            "============================================================\n\n"
        )
        with open(LOG_FILE_PATH, "a") as f:
            f.write(resume_note)


def format_total_size(total_bytes):
    """Formats a byte total for the inventory summary line, switching from MB to
    GB once the total reaches 1 GiB -- large DPX sequences commonly run past a
    terabyte, and reporting that in MB is unreadable."""
    gb = total_bytes / (1024 ** 3)
    if gb >= 1:
        return f"{round(gb, 2)} GB"
    return f"{round(total_bytes / (1024 ** 2), 2)} MB"


def detect_dpx_frame_gaps(dpx_files):
    """Best-effort check for missing frames in a DPX sequence: takes the last run
    of digits in each filename as its frame number and reports any integers
    missing from the resulting min-to-max range. Not foolproof for unusual
    naming schemes (it assumes the frame number is the last numeric run in the
    filename), but catches the common case of a straightforward gap."""
    frame_numbers = []
    for f in dpx_files:
        digits = re.findall(r'\d+', os.path.basename(f))
        if digits:
            frame_numbers.append(int(digits[-1]))
    if not frame_numbers:
        return []
    frame_numbers = sorted(set(frame_numbers))
    full_range = set(range(frame_numbers[0], frame_numbers[-1] + 1))
    return sorted(full_range - set(frame_numbers))


def has_sufficient_disk_space(total_source_bytes, available_bytes, margin=DEFAULT_DISK_SPACE_MARGIN):
    """Pure check: is `available_bytes` at least `total_source_bytes * margin`?
    See DEFAULT_DISK_SPACE_MARGIN for why source size (not a predicted MKV
    size) is the basis for the required-space floor."""
    return available_bytes >= total_source_bytes * margin


def get_inventory_total_bytes(log_path):
    """Reads a sequence's process log for the INVENTORY_TOTAL_BYTES marker
    Step 1 writes, returning the source (DPX+WAV) byte total recorded at
    inventory time, or None if it isn't present (an older log from before this
    marker existed, or a sequence where Step 1 never completed)."""
    marker = f">>> {INVENTORY_TOTAL_BYTES_MARKER}: "
    try:
        with open(log_path, "r") as f:
            for line in f:
                idx = line.find(marker)
                if idx != -1:
                    return int(line[idx + len(marker):].strip())
    except (OSError, ValueError):
        pass
    return None


def estimate_disk_space_margin(docs_dir, mkv_out_dir, exclude_sequence_name):
    """Estimates the disk-space margin for an upcoming sequence from the worst
    (least-compressed) FFv1 ratio actually achieved by this deployment's own
    prior sequences that fully completed (Step 10), rather than guessing a
    fixed ratio for all content -- a clean B&W scan can end up well under 50%
    of its DPX size, while a grainy color negative might barely compress at
    all, and there's no way to know upfront which a not-yet-processed sequence
    will be. Falls back to DEFAULT_DISK_SPACE_MARGIN if this deployment (this
    --docs-dir) has no completed sequences yet to learn from."""
    ratios = []
    for log_path in sorted(glob.glob(os.path.join(docs_dir, "*_process.log"))):
        name = os.path.basename(log_path)[:-len("_process.log")]
        if name == exclude_sequence_name:
            continue
        try:
            with open(log_path, "r") as f:
                content = f.read()
        except OSError:
            continue
        if ">>> SUCCESS: Step 10 completed successfully." not in content:
            continue
        total_bytes = get_inventory_total_bytes(log_path)
        if not total_bytes:
            continue
        try:
            mkv_bytes = os.path.getsize(os.path.join(mkv_out_dir, f"{name}.mkv"))
        except OSError:
            continue
        ratios.append(mkv_bytes / total_bytes)

    if not ratios:
        return DEFAULT_DISK_SPACE_MARGIN
    return max(ratios) * DISK_SPACE_HISTORICAL_PAD


def preflight_check(source_folder_path, source_folder_name, config: Config):
    """Best-effort Step 0 report, run before Step 1. Surfaces conditions that
    would otherwise only fail expensively late (missing metadata XML isn't hit
    until Step 7, after RAWcooked has already run Step 6; a missing MediaConch
    policy file isn't hit until Steps 3/4 try to invoke mediaconch against a path
    that doesn't exist; insufficient disk space on --output-dir isn't hit until
    Step 6 fails partway through a possibly hours-long encode) as hard failures,
    and reports sequence composition (audio Y/N, DPX frame count, best-effort
    frame-numbering gaps) and sidecar .md5 coverage as warnings only -- Step 2
    still halts with full per-file detail if sidecars are actually missing."""
    write_banner("0. PRE-FLIGHT CHECK")

    dpx_files = sorted(glob.glob(os.path.join(source_folder_path, "**/*.dpx"), recursive=True))
    wav_files = sorted(glob.glob(os.path.join(source_folder_path, "**/*.wav"), recursive=True))
    has_audio = bool(wav_files)

    write_log(f"  DPX frames found: {len(dpx_files)}")
    write_log(f"  Audio present: {'YES' if has_audio else 'NO'} ({len(wav_files)} .wav file(s))")

    gaps = detect_dpx_frame_gaps(dpx_files)
    if gaps:
        preview = ", ".join(str(g) for g in gaps[:10])
        more = f" (+{len(gaps) - 10} more)" if len(gaps) > 10 else ""
        write_log(f"  !!! WARNING: {len(gaps)} gap(s) detected in DPX frame numbering: {preview}{more}")
    elif dpx_files:
        write_log("  No gaps detected in DPX frame numbering.")

    total_source_bytes = sum(os.path.getsize(f) for f in dpx_files + wav_files)
    if config.disk_space_margin is not None:
        margin = config.disk_space_margin
        margin_source = "explicit --disk-space-margin"
    else:
        margin = estimate_disk_space_margin(config.docs_dir, config.mkv_out_dir, source_folder_name)
        if margin == DEFAULT_DISK_SPACE_MARGIN:
            margin_source = "no completed sequences yet in this deployment -- assumed ~1:1"
        else:
            margin_source = "estimated from prior sequences in this deployment"
    required_bytes = int(total_source_bytes * margin)
    try:
        available_bytes = shutil.disk_usage(config.mkv_out_dir).free
    except OSError as e:
        write_log(f"  !!! WARNING: could not check free space on {config.mkv_out_dir}: {e}")
    else:
        write_log(
            f"  Output drive free space: {format_total_size(available_bytes)} "
            f"(need ~{format_total_size(required_bytes)} at {margin:.2f}x source size, {margin_source})"
        )
        if not has_sufficient_disk_space(total_source_bytes, available_bytes, margin):
            write_log(f"  !!! INSUFFICIENT DISK SPACE on {config.mkv_out_dir}")
            return (
                f"Insufficient Disk Space on Output Drive: "
                f"{format_total_size(available_bytes)} free, ~{format_total_size(required_bytes)} needed"
            )

    custom_xml = os.path.join(config.docs_dir, f"{source_folder_name}.xml")
    if not os.path.exists(custom_xml):
        write_log(f"  !!! MISSING: metadata XML not found: {custom_xml}")
        return f"Missing Required XML Metadata: {custom_xml}"
    write_log(f"  Metadata XML: OK ({custom_xml})")

    if not os.path.exists(config.dpx_policy):
        write_log(f"  !!! MISSING: DPX MediaConch policy not found: {config.dpx_policy}")
        return f"Missing DPX MediaConch Policy: {config.dpx_policy}"
    write_log(f"  DPX MediaConch policy: OK ({config.dpx_policy})")

    if has_audio:
        if not os.path.exists(config.wav_policy):
            write_log(f"  !!! MISSING: WAV MediaConch policy not found: {config.wav_policy}")
            return f"Missing WAV MediaConch Policy: {config.wav_policy}"
        write_log(f"  WAV MediaConch policy: OK ({config.wav_policy})")

    # A sidecar isn't actually missing if Step 2 already relocated it to the
    # holding directory in a prior completed run -- mirror that step's own
    # resume check here so a resumed sequence doesn't get a false "missing
    # sidecar" warning for files that were correctly moved, not lost.
    sidecar_holding_dir = os.path.join(config.docs_dir, f"{source_folder_name}_source_md5_sidecars")
    missing_sidecars = 0
    for f in dpx_files + wav_files:
        if os.path.exists(f + ".md5"):
            continue
        rel_md5_path = os.path.relpath(f + ".md5", source_folder_path)
        if os.path.exists(os.path.join(sidecar_holding_dir, rel_md5_path)):
            continue
        missing_sidecars += 1
    if missing_sidecars:
        write_log(f"  !!! WARNING: {missing_sidecars} file(s) missing a sidecar .md5 (Step 2 will halt on these).")
    else:
        write_log("  All source files have sidecar .md5 checksums.")

    write_log("")
    return True


def build_rawcooked_log_header(completion_time=None):
    """Builds the header banner inserted at the top of the RAWcooked .log file
    once encoding finishes, so the sidecar log is self-identifying without
    needing the process log alongside it."""
    completion_time = completion_time or datetime.datetime.now()
    ts = completion_time.strftime("%y/%m/%d, %I:%M %p")
    return (
        "============================================================\n"
        "Stanford Media Preservation Lab\n"
        f"RAWcooked processing completed {ts}\n"
        "============================================================\n\n"
    )


def prepend_rawcooked_log_header(rc_log_path):
    with open(rc_log_path, "r") as f:
        original = f.read()
    with open(rc_log_path, "w") as f:
        f.write(build_rawcooked_log_header() + original)


def run_mediaconch(policy_path, files, csv_format=False):
    """Runs mediaconch once against a list of files. Uses a fresh, auto-cleaned
    HOME directory per call (mediaconch reads/writes user-specific config/cache
    under HOME) instead of the shell-based `HOME=$(mktemp -d) ...` this used to
    rely on — that spawned an extra bash + mktemp process per call and never
    cleaned up the temp directory it created."""
    args = ["mediaconch", "-p", policy_path]
    if csv_format:
        args.append("-fc")
    args.extend(files)
    with tempfile.TemporaryDirectory(prefix="srw-mediaconch-home-") as home_dir:
        env = {**os.environ, "HOME": home_dir}
        return subprocess.run(args, capture_output=True, text=True, env=env)


def validate_files_against_policy(files, policy_path, batch_size=DEFAULT_MEDIACONCH_BATCH_SIZE):
    """Validates files against a MediaConch policy in batches of one subprocess
    call per `batch_size` files (rather than one call per file), yielding
    (file_path, is_valid) in the original file order. Batches are only run as
    the caller actually consumes them, so a caller that stops at the first
    invalid file (as process_sequence does) never pays for later batches."""
    for i in range(0, len(files), batch_size):
        chunk = files[i:i + batch_size]
        result = run_mediaconch(policy_path, chunk, csv_format=True)
        passed = set()
        reader = csv.DictReader(io.StringIO(result.stdout))
        for row in reader:
            if row.get("overall") == "pass":
                passed.add(row["filename"])
        for f in chunk:
            yield f, f in passed


def verify_md5(file_path, md5_path):
    file_name = os.path.basename(file_path)
    try:
        with open(md5_path, 'r') as f:
            expected = f.read().split()[0].lower()
        sha = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha.update(chunk)
        actual = sha.hexdigest()

        if expected == actual:
            write_log(f"  [PASS] {file_name}")
            return True
        else:
            write_log(f"  [FAIL] !!! {file_name} (Mismatch!)")
            return False
    except Exception as e:
        write_log(f"  [ERROR] {file_name}: {str(e)}")
        return False


def run_step_verbose(step_num, step_name, command, parse_rawcooked=False):
    """Run a command with verbose output, checking if already complete first."""
    marker = f"Step {step_num}"
    if is_step_complete(marker):
        write_log(f"Step {step_num} ({step_name}) already completed. Skipping...")
        return True

    write_banner(f"{step_num}. {step_name}")
    try:
        process = subprocess.Popen(
            command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, executable='/bin/bash'
        )
        output_lines = []

        current_phase = None
        phase_markers = {
            'Reversibility data': '5a',
            'Encoding': '5b',
            'Reversibility check': '5c'
        }

        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()

            if parse_rawcooked:
                for keyword, phase in phase_markers.items():
                    if keyword in line and current_phase != phase:
                        current_phase = phase
                        phase_name = {
                            '5a': 'DPX FILE ANALYSIS',
                            '5b': 'RAWCOOKED LOSSLESS ENCODING',
                            '5c': 'RAWCOOKED REVERSIBILITY CHECK'
                        }[phase]
                        divider = f"\n--- {phase_name} ---\n"
                        output_lines.append(divider)
                        sys.stdout.write(divider)
                        sys.stdout.flush()
                        break

            output_lines.append(line)

        process.wait()

        if LOG_FILE_PATH:
            with open(LOG_FILE_PATH, "a") as f:
                f.writelines(output_lines)

        if process.returncode == 0:
            mark_step_complete(step_num)
            return True
        else:
            write_log(f"!!! ERROR: Step {step_num} failed with return code {process.returncode}")
            return False
    except Exception as e:
        write_log(f"!!! EXCEPTION in Step {step_num}: {e}")
        return False


def run_mediainfo_step(step_num, mkv_path, mediainfo_out_path):
    """Runs `mediainfo -f -i` against the finished MKV and writes the full
    technical metadata dump to a text file in --docs-dir, for downstream QC
    review and database ingest. The same output is also appended to the process
    log for the log's own completeness."""
    marker = f"Step {step_num}"
    if is_step_complete(marker):
        write_log(f"Step {step_num} (MediaInfo Technical Metadata) already completed. Skipping...")
        return True

    write_banner(f"{step_num}. MEDIAINFO TECHNICAL METADATA")
    try:
        result = subprocess.run(["mediainfo", "-f", "-i", mkv_path], capture_output=True, text=True)
        with open(mediainfo_out_path, "w") as f:
            f.write(result.stdout)
        if LOG_FILE_PATH:
            with open(LOG_FILE_PATH, "a") as f:
                f.write(result.stdout)

        if result.returncode == 0:
            write_log(f"  [DONE] Wrote {os.path.basename(mediainfo_out_path)}")
            mark_step_complete(step_num)
            return True
        else:
            write_log(f"!!! ERROR: Step {step_num} failed with return code {result.returncode}")
            if result.stderr.strip():
                write_log(result.stderr.strip())
            return False
    except Exception as e:
        write_log(f"!!! EXCEPTION in Step {step_num}: {e}")
        return False


def process_sequence(source_folder_path, config: Config):
    global LOG_FILE_PATH

    source_folder_name = os.path.basename(source_folder_path.rstrip(os.sep))
    LOG_FILE_PATH = os.path.join(config.docs_dir, f"{source_folder_name}_process.log")

    init_log_header()
    write_log(f"PROCESSING FOLDER: {source_folder_name}")

    step_log = []

    csv_path = os.path.join(config.docs_dir, f"{source_folder_name}_inventory.csv")
    custom_xml = os.path.join(config.docs_dir, f"{source_folder_name}.xml")
    mkv_path = os.path.join(config.mkv_out_dir, f"{source_folder_name}.mkv")
    mp4_out = os.path.join(config.mkv_out_dir, f"{source_folder_name}_rawcooked_review.mp4")
    mediainfo_path = os.path.join(config.docs_dir, f"{source_folder_name}_mediainfo.txt")

    # Original per-file sidecar .md5s are relocated here (not deleted) after step 2
    # verifies them -- they have to leave the source folder before step 6, since
    # rawcooked --all would otherwise try to embed each one as its own MKV
    # attachment, but they're kept as a permanent baseline for re-verifying the
    # original transfer later if ever needed.
    sidecar_holding_dir = os.path.join(config.docs_dir, f"{source_folder_name}_source_md5_sidecars")

    # .md5 manifest goes into the source folder, named after the MKV output,
    # so RAWcooked --all will embed it natively during the encode
    long_md5_path = os.path.join(source_folder_path, f"{source_folder_name}.md5")

    # RAWcooked .log is output next to the MKV (no longer embedded as attachment)
    rc_log_path = os.path.join(config.mkv_out_dir, f"{source_folder_name}.log")

    # 0. Pre-flight check
    if not config.skip_preflight:
        preflight_result = preflight_check(source_folder_path, source_folder_name, config)
        if preflight_result is not True:
            return preflight_result

    # --start-step lets a run skip ahead past steps the caller is already certain
    # passed (e.g. the process log was lost to a drive failure, or the job failed
    # on a different machine) -- it marks them complete in-place so every step
    # block below's existing is_step_complete() check naturally skips them,
    # without needing per-step special-casing.
    if config.start_step > 1:
        write_log(f"--start-step {config.start_step}: treating steps 1-{config.start_step - 1} as already complete (not verified this run).")
        if config.start_step > 2:
            write_log(
                "  NOTE: Step 2 relocates sidecar .md5 files out of the source folder; "
                "skipping it via --start-step does NOT do this automatically. Ensure the "
                "source folder has no stray sidecars before Step 6 runs, or RAWcooked --all "
                "will try to embed each one as its own MKV attachment."
            )
        for n in range(1, config.start_step):
            if not is_step_complete(f"Step {n}"):
                mark_step_complete(n)
        write_log("")

    # 1. Inventory
    if is_step_complete("Step 1"):
        write_log("Step 1 (Inventory) already completed. Skipping...")
        step_log.append((1, "INVENTORY GENERATION", get_step_completion_timestamp(1)))
    else:
        write_banner("1. INVENTORY GENERATION")
        dpx_count = 0
        total_bytes = 0
        try:
            with open(csv_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['path', 'file name', 'extension', 'file size (MB)', 'file size (bytes)'])
                for root, dirs, files in os.walk(source_folder_path):
                    for file in sorted(files):
                        if not file.startswith('.'):
                            f_full_path = os.path.join(root, file)
                            ext = os.path.splitext(file)[1].lower()
                            if ext == '.dpx':
                                dpx_count += 1
                            size_bytes = os.path.getsize(f_full_path)
                            total_bytes += size_bytes
                            size_mb = round(size_bytes / 1048576, 2)

                            # For .md5 files, show bytes instead of MB (which would be 0.00)
                            if ext == '.md5':
                                writer.writerow([f_full_path, file, ext, '', size_bytes])
                            else:
                                writer.writerow([f_full_path, file, ext, size_mb, ''])
            write_log(f"Inventory complete. Found {dpx_count} DPX files. Total: {format_total_size(total_bytes)}")
            write_log(f">>> {INVENTORY_TOTAL_BYTES_MARKER}: {total_bytes}", print_to_screen=False)
            mark_step_complete(1)
            step_log.append((1, "INVENTORY GENERATION", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        except Exception as e:
            write_log(f"!!! ERROR in Step 1: {e}")
            return "Inventory Generation Error"
    write_log("")
    if config.end_step == 1:
        write_log("Stopping after Step 1 (--end-step 1).")
        return True

    # 2. Source Integrity
    if is_step_complete("Step 2"):
        write_log("Step 2 (Checksums) already completed. Skipping...")
        step_log.append((2, "CHECKSUM VERIFICATION", get_step_completion_timestamp(2)))
    else:
        write_banner("2. CHECKSUM VERIFICATION")
        try:
            moved_count = 0
            for root, dirs, files in os.walk(source_folder_path):
                for file in sorted(files):
                    if not file.endswith('.md5') and not file.startswith('.'):
                        f_path = os.path.join(root, file)
                        m_path = f_path + ".md5"
                        rel_md5_path = os.path.relpath(m_path, source_folder_path)
                        holding_path = os.path.join(sidecar_holding_dir, rel_md5_path)

                        if os.path.exists(holding_path) and not os.path.exists(m_path):
                            # Already verified and relocated in a prior attempt that
                            # was interrupted partway through this step -- don't
                            # re-verify or report it missing.
                            continue
                        if not os.path.exists(m_path):
                            write_log(f"CRITICAL ERROR: Missing MD5 for {file}")
                            return "Missing MD5"
                        if not verify_md5(f_path, m_path):
                            return "Checksum Mismatch"
                        os.makedirs(os.path.dirname(holding_path), exist_ok=True)
                        shutil.move(m_path, holding_path)
                        moved_count += 1
            write_log("-" * 30)
            write_log(f"Relocated {moved_count} sidecar .md5 file(s) to: {sidecar_holding_dir}")
            write_log("(kept as a permanent baseline for re-verifying the original transfer -- not deleted)")
            mark_step_complete(2)
            step_log.append((2, "CHECKSUM VERIFICATION", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        except Exception as e:
            write_log(f"!!! ERROR in Step 2: {e}")
            return "Checksum Verification Error"
    write_log("")
    if config.end_step == 2:
        write_log("Stopping after Step 2 (--end-step 2).")
        return True

    # 3. MediaConch DPX Validation
    if is_step_complete("Step 3"):
        write_log("Step 3 (MediaConch DPX) already completed. Skipping...")
        step_log.append((3, "MEDIACONCH DPX VALIDATION", get_step_completion_timestamp(3)))
    else:
        write_banner("3. MEDIACONCH DPX VALIDATION")
        try:
            dpx_files = sorted(glob.glob(os.path.join(source_folder_path, "**/*.dpx"), recursive=True))
            for dpx, is_valid in validate_files_against_policy(dpx_files, config.dpx_policy, config.mediaconch_batch_size):
                f_name = os.path.basename(dpx)
                if not is_valid:
                    # Re-run just this file for the full human-readable policy detail
                    res = run_mediaconch(config.dpx_policy, [dpx])
                    write_log(f"  [INVALID] {f_name}")
                    if res.stdout.strip():
                        write_log(f"  [MEDIACONCH OUTPUT]\n{res.stdout.strip()}")
                    if res.stderr.strip():
                        write_log(f"  [MEDIACONCH STDERR]\n{res.stderr.strip()}")
                    return "MediaConch DPX Policy Failure"
                write_log(f"  [VALID] {f_name}")
            mark_step_complete(3)
            step_log.append((3, "MEDIACONCH DPX VALIDATION", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        except Exception as e:
            write_log(f"!!! ERROR in Step 3: {e}")
            return "MediaConch DPX Validation Error"
    write_log("")
    if config.end_step == 3:
        write_log("Stopping after Step 3 (--end-step 3).")
        return True

    # 4. MediaConch WAV Validation
    if is_step_complete("Step 4"):
        write_log("Step 4 (MediaConch WAV) already completed. Skipping...")
        step_log.append((4, "MEDIACONCH WAV VALIDATION", get_step_completion_timestamp(4)))
    else:
        write_banner("4. MEDIACONCH WAV VALIDATION")
        try:
            wav_files = sorted(glob.glob(os.path.join(source_folder_path, "**/*.wav"), recursive=True))
            if not wav_files:
                write_log("  No .wav files found (picture only). Skipping validation.")
            else:
                for wav, is_valid in validate_files_against_policy(wav_files, config.wav_policy, config.mediaconch_batch_size):
                    f_name = os.path.basename(wav)
                    if not is_valid:
                        res = run_mediaconch(config.wav_policy, [wav])
                        write_log(f"  [INVALID] {f_name}")
                        if res.stdout.strip():
                            write_log(f"  [MEDIACONCH OUTPUT]\n{res.stdout.strip()}")
                        if res.stderr.strip():
                            write_log(f"  [MEDIACONCH STDERR]\n{res.stderr.strip()}")
                        return "MediaConch WAV Policy Failure"
                    write_log(f"  [VALID] {f_name}")
            mark_step_complete(4)
            step_log.append((4, "MEDIACONCH WAV VALIDATION", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        except Exception as e:
            write_log(f"!!! ERROR in Step 4: {e}")
            return "MediaConch WAV Validation Error"
    write_log("")
    if config.end_step == 4:
        write_log("Stopping after Step 4 (--end-step 4).")
        return True

    # 5. Manifest Generation
    if is_step_complete("Step 5"):
        write_log("Step 5 (Manifest) already completed. Skipping...")
        step_log.append((5, "MANIFEST GENERATION (.md5)", get_step_completion_timestamp(5)))
    else:
        write_banner("5. MANIFEST GENERATION (.md5)")
        write_log(f"  Writing manifest to source folder: {long_md5_path}")
        try:
            with open(long_md5_path, 'w') as lf:
                for root, dirs, files in os.walk(source_folder_path):
                    for file in sorted(files):
                        if not file.startswith('.') and not file.endswith('.md5'):
                            sha = hashlib.md5()
                            with open(os.path.join(root, file), 'rb') as f:
                                for chunk in iter(lambda: f.read(8192), b""):
                                    sha.update(chunk)
                            lf.write(f"{sha.hexdigest()}  {file}\n")
            mark_step_complete(5)
            step_log.append((5, "MANIFEST GENERATION (.md5)", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        except Exception as e:
            write_log(f"!!! ERROR in Step 5: {e}")
            return "Manifest Generation Error"
    write_log("")
    if config.end_step == 5:
        write_log("Stopping after Step 5 (--end-step 5).")
        return True

    # 6. RAWcooked Transcode
    rc_cmd = (
        f"rawcooked -y --all -s {config.attachment_size} --log-name '{rc_log_path}' "
        f"'{source_folder_path}' -o '{mkv_path}'"
    )

    step6_already_done = is_step_complete("Step 6")
    if not run_step_verbose(6, "RAWCOOKED TRANSCODE", rc_cmd, parse_rawcooked=True):
        return "RAWcooked Encoding Error"
    step_log.append((6, "RAWCOOKED TRANSCODE", get_step_completion_timestamp(6)))

    if not step6_already_done and os.path.exists(rc_log_path):
        prepend_rawcooked_log_header(rc_log_path)

    if os.path.exists(rc_log_path):
        with open(rc_log_path, 'r') as rcl:
            for line in rcl:
                if "?" in line or "reversibility check failed" in line.lower():
                    write_log(f"!!! NOTE: RAWcooked bypassed a prompt: {line.strip()}")
    write_log("")
    if config.end_step == 6:
        write_log("Stopping after Step 6 (--end-step 6).")
        return True

    # 7. Metadata Tags
    if not os.path.exists(custom_xml):
        write_log(f"CRITICAL: Missing '{source_folder_name}.xml'")
        return "Missing Required XML Metadata"

    if not run_step_verbose(7, "EMBED METADATA TAGS", f"mkvpropedit '{mkv_path}' --tags all:'{custom_xml}'"):
        return "mkvpropedit Tagging Error"
    step_log.append((7, "EMBED METADATA TAGS", get_step_completion_timestamp(7)))
    write_log("")
    if config.end_step == 7:
        write_log("Stopping after Step 7 (--end-step 7).")
        return True

    # 8. FFmpeg Review Copy
    if not run_step_verbose(8, "GENERATE REVIEW DERIVATIVE", f"ffmpeg -i '{mkv_path}' -crf 18 -vf 'scale=-2:720' -pix_fmt yuv420p '{mp4_out}'"):
        return "FFmpeg Derivative Error"
    step_log.append((8, "GENERATE REVIEW DERIVATIVE", get_step_completion_timestamp(8)))
    write_log("")
    if config.end_step == 8:
        write_log("Stopping after Step 8 (--end-step 8).")
        return True

    # 9. MediaInfo Technical Metadata
    if not run_mediainfo_step(9, mkv_path, mediainfo_path):
        return "MediaInfo Metadata Error"
    step_log.append((9, "MEDIAINFO TECHNICAL METADATA", get_step_completion_timestamp(9)))
    write_log("")
    if config.end_step == 9:
        write_log("Stopping after Step 9 (--end-step 9).")
        return True

    # --- PROCESS SUMMARY (Steps 1-9) ---
    summary_timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    summary_lines = []
    summary_lines.append(f"[{summary_timestamp}] \n{'='*60}")
    summary_lines.append(f"[{summary_timestamp}]  PROCESS SUMMARY")
    summary_lines.append(f"[{summary_timestamp}] {'='*60}")
    for step_num, step_name, step_ts in step_log:
        summary_lines.append(f"[{step_ts}]  STEP: {step_num}. {step_name} - completed successfully")
    summary_lines.append(f"[{summary_timestamp}] ** All processes completed successfully **")
    summary_lines.append(f"[{summary_timestamp}] ** Starting md5 checksum generation **")
    summary_lines.append(f"[{summary_timestamp}] {'='*60}\n")

    summary_text = "\n".join(summary_lines)
    print(summary_text)
    if LOG_FILE_PATH:
        with open(LOG_FILE_PATH, "a") as f:
            f.write(summary_text + "\n")

    # 10. Final Deliverable Hashes (MKV and RAWcooked log)
    if is_step_complete("Step 10"):
        write_log("Step 10 (Final Hashes) already completed. Skipping...")
    else:
        write_banner("10. FINAL DELIVERABLE HASHES")
        try:
            sha = hashlib.md5()
            with open(mkv_path, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    sha.update(chunk)
            with open(mkv_path + ".md5", 'w') as f:
                f.write(f"{sha.hexdigest()}  {os.path.basename(mkv_path)}\n")
            write_log(f"  [DONE] {os.path.basename(mkv_path)}")

            if os.path.exists(rc_log_path):
                sha = hashlib.md5()
                with open(rc_log_path, 'rb') as f:
                    for chunk in iter(lambda: f.read(8192), b""):
                        sha.update(chunk)
                with open(rc_log_path + ".md5", 'w') as f:
                    f.write(f"{sha.hexdigest()}  {os.path.basename(rc_log_path)}\n")
                write_log(f"  [DONE] {os.path.basename(rc_log_path)}")

            mark_step_complete(10)
        except Exception as e:
            write_log(f"!!! ERROR in Step 10: {e}")
            return "Final Hash Generation Error"

    write_log(f"\n{'='*60}")
    write_log(f"COMPLETED PROCESS: {source_folder_name}")
    write_log(f"{'='*60}\n")
    return True


def add_run_arguments(parser):
    """Attach the flags for single-directory-set processing to a parser (or subparser)."""
    parser.epilog = RUN_EPILOG
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    parser.add_argument(
        "--source-dir", default="Source",
        help="Parent directory containing one subfolder per DPX sequence (default: ./Source)",
    )
    parser.add_argument(
        "--output-dir", required=True,
        help="Destination directory for MKV, RAWcooked log, checksum, and MP4 review files",
    )
    parser.add_argument(
        "--docs-dir", default="Documents",
        help="Directory for per-sequence process logs, inventory CSVs, and required XML metadata (default: ./Documents)",
    )
    parser.add_argument(
        "--mediaconch-dir", default="MediaConch",
        help="Directory containing MediaConch policy XML files (default: ./MediaConch)",
    )
    parser.add_argument(
        "--dpx-policy", default=None,
        help="Path to the DPX MediaConch policy XML (default: <mediaconch-dir>/DPX_SMPTE-CORE.xml)",
    )
    parser.add_argument(
        "--wav-policy", default=None,
        help="Path to the WAV MediaConch policy XML (default: <mediaconch-dir>/WAV_policy.xml)",
    )
    parser.add_argument(
        "--attachment-size", type=int, default=DEFAULT_ATTACHMENT_SIZE,
        help=f"RAWcooked attachment size limit in bytes, passed as -s (default: {DEFAULT_ATTACHMENT_SIZE})",
    )
    parser.add_argument(
        "--mediaconch-batch-size", type=int, default=DEFAULT_MEDIACONCH_BATCH_SIZE,
        help=f"Number of files validated per MediaConch invocation, instead of one process per file (default: {DEFAULT_MEDIACONCH_BATCH_SIZE})",
    )
    parser.add_argument(
        "--start-step", type=int, default=1,
        help=(
            "Skip steps before this one, treating them as already complete without "
            "verifying the log (default: 1, i.e. run everything). Use when a prior "
            "step's completion is certain but no process log is available -- e.g. a "
            "lost/corrupted log, or a job that failed on a different machine."
        ),
    )
    parser.add_argument(
        "--end-step", type=int, default=TOTAL_STEPS,
        help=(
            f"Stop after this step instead of continuing to the end (default: {TOTAL_STEPS}, "
            "the full workflow). Combine with --start-step to run an isolated range, e.g. "
            "--start-step 3 --end-step 4 to run only MediaConch validation on its own."
        ),
    )
    parser.add_argument(
        "--skip-preflight", action="store_true",
        help=(
            "Skip the Step 0 pre-flight check (metadata XML / MediaConch policy "
            "presence, output-drive disk space, audio detection, DPX frame-gap "
            "scan, sidecar .md5 coverage)."
        ),
    )
    parser.add_argument(
        "--disk-space-margin", type=float, default=None,
        help=(
            "Required free space on --output-dir, as an explicit multiple of "
            "total source (DPX+WAV) size. By default srw estimates this "
            "instead, from the worst FFv1 compression ratio actually achieved "
            "by prior completed sequences in this --docs-dir (falling back to "
            f"an assumed ~1:1 ratio, {DEFAULT_DISK_SPACE_MARGIN}x, only for the "
            "very first sequence in a fresh deployment). Set this to override "
            "that estimate with a fixed value."
        ),
    )
    return parser


def build_run_arg_parser():
    """Standalone parser for `python -m srw.cli` / direct run_main(argv) use and tests."""
    parser = argparse.ArgumentParser(
        prog="srw run",
        description="Process one set of Source/Documents/MediaConch directories.",
    )
    return add_run_arguments(parser)


def tools_needed_for_range(start_step, end_step):
    """Pure helper: which (tool, apt_package) pairs a given --start-step/--end-step
    range actually touches, so a narrow run (e.g. MediaConch validation only)
    isn't blocked by tools it will never call."""
    seen = {}
    for step in range(start_step, end_step + 1):
        if step in STEP_DEPENDENCIES:
            tool, pkg = STEP_DEPENDENCIES[step]
            seen[tool] = pkg
    return sorted(seen.items())


def check_dependencies(start_step=1, end_step=TOTAL_STEPS):
    """Verify external tools needed by the given step range are on PATH. Exits
    the process if any are missing."""
    for tool, apt_package in tools_needed_for_range(start_step, end_step):
        if not shutil.which(tool):
            print(f"FATAL ERROR: {tool} not installed. Please run: sudo apt install {apt_package}")
            sys.exit(1)


def run_main(args):
    """Runs the single-directory-set workflow. `args` is a parsed argparse.Namespace
    with the attributes defined by add_run_arguments() (source_dir, output_dir, docs_dir,
    mediaconch_dir, dpx_policy, wav_policy, attachment_size, mediaconch_batch_size,
    start_step, end_step, skip_preflight, disk_space_margin)."""
    if not (1 <= args.start_step <= TOTAL_STEPS) or not (1 <= args.end_step <= TOTAL_STEPS):
        print(f"FATAL ERROR: --start-step and --end-step must be between 1 and {TOTAL_STEPS}.")
        sys.exit(1)
    if args.start_step > args.end_step:
        print("FATAL ERROR: --start-step cannot be greater than --end-step.")
        sys.exit(1)
    if args.disk_space_margin is not None and args.disk_space_margin <= 0:
        print("FATAL ERROR: --disk-space-margin must be greater than 0.")
        sys.exit(1)

    mc_dir = args.mediaconch_dir
    config = Config(
        source_parent=args.source_dir,
        mkv_out_dir=args.output_dir,
        docs_dir=args.docs_dir,
        mc_dir=mc_dir,
        dpx_policy=args.dpx_policy or os.path.join(mc_dir, "DPX_SMPTE-CORE.xml"),
        wav_policy=args.wav_policy or os.path.join(mc_dir, "WAV_policy.xml"),
        attachment_size=args.attachment_size,
        mediaconch_batch_size=args.mediaconch_batch_size,
        start_step=args.start_step,
        end_step=args.end_step,
        skip_preflight=args.skip_preflight,
        disk_space_margin=args.disk_space_margin,
    )

    if not os.path.exists(config.docs_dir):
        os.makedirs(config.docs_dir)
    if not os.path.exists(config.mkv_out_dir):
        os.makedirs(config.mkv_out_dir, exist_ok=True)
    check_dependencies(config.start_step, config.end_step)

    if not os.path.isdir(config.source_parent):
        print(f"FATAL ERROR: source directory not found: {config.source_parent}")
        sys.exit(1)

    all_sequences = sorted([
        os.path.join(config.source_parent, d) for d in os.listdir(config.source_parent)
        if os.path.isdir(os.path.join(config.source_parent, d)) and not d.startswith('.')
    ])

    if not all_sequences:
        print(f"No folders found in {config.source_parent}")
        return 0

    print("=" * 60)
    print("Stanford Media Preservation Lab - srw Workflow Active")
    print("Resumable Mode: Any failed step can be resumed")
    print("=" * 60)

    success_list, error_list = [], []

    for seq in all_sequences:
        folder_name = os.path.basename(seq.rstrip(os.sep))
        print(f"\n>>> Processing: {folder_name}")
        result = process_sequence(seq, config)
        if result is True:
            success_list.append(folder_name)
        else:
            error_list.append((folder_name, result))
            print(f"\n!!! Processing halted at: {folder_name}")
            print(f"!!! Reason: {result}")
            print(f"!!! To resume: Simply run this command again")
            break

    print("\n" + "=" * 60)
    print("BATCH PROCESSING SUMMARY")
    print("=" * 60)
    print(f"Total Sequences: {len(all_sequences)}")
    print(f"Successfully Completed: {len(success_list)}")

    if success_list:
        print("\nCompleted:")
        for s in success_list:
            print(f"  [OK] {s}")

    if error_list:
        print(f"\nErrors/Halted: {len(error_list)}")
        for name, err in error_list:
            print(f"  [FAIL] {name}: {err}")
        print("\nTo resume: Fix the issue and run the command again.")
        print("Already-completed steps will be automatically skipped.")

    print("=" * 60 + "\n")

    return 1 if error_list else 0


def build_arg_parser():
    """Top-level parser with `run` and `batch` subcommands."""
    parser = argparse.ArgumentParser(
        prog="srw",
        description="SMPL RAWcooked Workflow — batch DPX to FFv1/MKV preservation transcoding.",
        epilog=TOP_LEVEL_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"srw {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run", help="Process one set of Source/Documents/MediaConch directories.",
    )
    add_run_arguments(run_parser)
    run_parser.set_defaults(func=run_main)

    # srw.batch is imported lazily inside main() so `srw run ...` doesn't need
    # tomllib/the batch module loaded for the common case.
    batch_parser = subparsers.add_parser(
        "batch", help="Launch multiple `srw run` invocations in parallel from a TOML config.",
    )
    from srw.batch import add_batch_arguments, batch_main
    add_batch_arguments(batch_parser)
    batch_parser.set_defaults(func=batch_main)

    return parser


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
