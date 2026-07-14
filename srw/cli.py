"""
srw.cli — core logic for the SMPL RAWcooked Workflow tool.

Stanford Media Preservation Lab
DPX --> FFv1/MKV batch preservation transcoding, with MD5 verification,
MediaConch policy validation, and a resumable nine-step per-sequence log.
"""

import os
import csv
import hashlib
import subprocess
import datetime
import glob
import shutil
import sys
import argparse
from dataclasses import dataclass

__version__ = "1.1.0"

DEFAULT_ATTACHMENT_SIZE = 5_000_000  # 5 MB; RAWcooked's own default is 1 MB


@dataclass
class Config:
    source_parent: str
    mkv_out_dir: str
    docs_dir: str
    mc_dir: str
    dpx_policy: str
    wav_policy: str
    attachment_size: int


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

    # .md5 manifest goes into the source folder, named after the MKV output,
    # so RAWcooked --all will embed it natively during the encode
    long_md5_path = os.path.join(source_folder_path, f"{source_folder_name}.md5")

    # RAWcooked .log is output next to the MKV (no longer embedded as attachment)
    rc_log_path = os.path.join(config.mkv_out_dir, f"{source_folder_name}.log")

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
            total_mb = round(total_bytes / 1048576, 2)
            write_log(f"Inventory complete. Found {dpx_count} DPX files. Total: {total_mb} MB")
            mark_step_complete(1)
            step_log.append((1, "INVENTORY GENERATION", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        except Exception as e:
            write_log(f"!!! ERROR in Step 1: {e}")
            return "Inventory Generation Error"
    write_log("")

    # 2. Source Integrity
    if is_step_complete("Step 2"):
        write_log("Step 2 (Checksums) already completed. Skipping...")
        step_log.append((2, "CHECKSUM VERIFICATION", get_step_completion_timestamp(2)))
    else:
        write_banner("2. CHECKSUM VERIFICATION")
        try:
            md5_files_to_delete = []
            for root, dirs, files in os.walk(source_folder_path):
                for file in sorted(files):
                    if not file.endswith('.md5') and not file.startswith('.'):
                        f_path = os.path.join(root, file)
                        m_path = f_path + ".md5"
                        if not os.path.exists(m_path):
                            write_log(f"CRITICAL ERROR: Missing MD5 for {file}")
                            return "Missing MD5"
                        if not verify_md5(f_path, m_path):
                            return "Checksum Mismatch"
                        md5_files_to_delete.append(m_path)
            write_log("-" * 30)
            write_log("Deleting sidecar .md5 files...")
            for m_file in md5_files_to_delete:
                os.remove(m_file)
            mark_step_complete(2)
            step_log.append((2, "CHECKSUM VERIFICATION", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        except Exception as e:
            write_log(f"!!! ERROR in Step 2: {e}")
            return "Checksum Verification Error"
    write_log("")

    # 3. MediaConch DPX Validation
    if is_step_complete("Step 3"):
        write_log("Step 3 (MediaConch DPX) already completed. Skipping...")
        step_log.append((3, "MEDIACONCH DPX VALIDATION", get_step_completion_timestamp(3)))
    else:
        write_banner("3. MEDIACONCH DPX VALIDATION")
        try:
            dpx_files = sorted(glob.glob(os.path.join(source_folder_path, "**/*.dpx"), recursive=True))
            for dpx in dpx_files:
                f_name = os.path.basename(dpx)
                mc_cmd = f"HOME=$(mktemp -d) mediaconch -p '{config.dpx_policy}' '{dpx}'"
                res = subprocess.run(mc_cmd, shell=True, capture_output=True, text=True, executable='/bin/bash')
                if res.returncode != 0 or "fail!" in res.stdout:
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
                for wav in wav_files:
                    f_name = os.path.basename(wav)
                    mc_cmd = f"HOME=$(mktemp -d) mediaconch -p '{config.wav_policy}' '{wav}'"
                    res = subprocess.run(mc_cmd, shell=True, capture_output=True, text=True, executable='/bin/bash')
                    if res.returncode != 0 or "fail!" in res.stdout:
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

    # 6. RAWcooked Transcode
    rc_cmd = (
        f"rawcooked -y --all -s {config.attachment_size} --log-name '{rc_log_path}' "
        f"'{source_folder_path}' -o '{mkv_path}'"
    )

    if not run_step_verbose(6, "RAWCOOKED TRANSCODE", rc_cmd, parse_rawcooked=True):
        return "RAWcooked Encoding Error"
    step_log.append((6, "RAWCOOKED TRANSCODE", get_step_completion_timestamp(6)))

    if os.path.exists(rc_log_path):
        with open(rc_log_path, 'r') as rcl:
            for line in rcl:
                if "?" in line or "reversibility check failed" in line.lower():
                    write_log(f"!!! NOTE: RAWcooked bypassed a prompt: {line.strip()}")
    write_log("")

    # 7. Metadata Tags
    if not os.path.exists(custom_xml):
        write_log(f"CRITICAL: Missing '{source_folder_name}.xml'")
        return "Missing Required XML Metadata"

    if not run_step_verbose(7, "EMBED METADATA TAGS", f"mkvpropedit '{mkv_path}' --tags all:'{custom_xml}'"):
        return "mkvpropedit Tagging Error"
    step_log.append((7, "EMBED METADATA TAGS", get_step_completion_timestamp(7)))
    write_log("")

    # 8. FFmpeg Review Copy
    if not run_step_verbose(8, "GENERATE REVIEW DERIVATIVE", f"ffmpeg -i '{mkv_path}' -crf 18 -vf 'scale=-2:720' -pix_fmt yuv420p '{mp4_out}'"):
        return "FFmpeg Derivative Error"
    step_log.append((8, "GENERATE REVIEW DERIVATIVE", get_step_completion_timestamp(8)))
    write_log("")

    # --- PROCESS SUMMARY (Steps 1-8) ---
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

    # 9. Final Deliverable Hashes (MKV and RAWcooked log)
    if is_step_complete("Step 9"):
        write_log("Step 9 (Final Hashes) already completed. Skipping...")
    else:
        write_banner("9. FINAL DELIVERABLE HASHES")
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

            mark_step_complete(9)
        except Exception as e:
            write_log(f"!!! ERROR in Step 9: {e}")
            return "Final Hash Generation Error"

    write_log(f"\n{'='*60}")
    write_log(f"COMPLETED PROCESS: {source_folder_name}")
    write_log(f"{'='*60}\n")
    return True


def add_run_arguments(parser):
    """Attach the flags for single-directory-set processing to a parser (or subparser)."""
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
    return parser


def build_run_arg_parser():
    """Standalone parser for `python -m srw.cli` / direct run_main(argv) use and tests."""
    parser = argparse.ArgumentParser(
        prog="srw run",
        description="Process one set of Source/Documents/MediaConch directories.",
    )
    return add_run_arguments(parser)


def check_dependencies():
    """Verify required external tools are on PATH. Exits the process if any are missing."""
    for tool, apt_package in (
        ("mkvpropedit", "mkvtoolnix"),
        ("rawcooked", "rawcooked"),
        ("mediaconch", "mediaconch"),
    ):
        if not shutil.which(tool):
            print(f"FATAL ERROR: {tool} not installed. Please run: sudo apt install {apt_package}")
            sys.exit(1)


def run_main(args):
    """Runs the single-directory-set workflow. `args` is a parsed argparse.Namespace
    with the attributes defined by add_run_arguments() (source_dir, output_dir, docs_dir,
    mediaconch_dir, dpx_policy, wav_policy, attachment_size)."""
    mc_dir = args.mediaconch_dir
    config = Config(
        source_parent=args.source_dir,
        mkv_out_dir=args.output_dir,
        docs_dir=args.docs_dir,
        mc_dir=mc_dir,
        dpx_policy=args.dpx_policy or os.path.join(mc_dir, "DPX_SMPTE-CORE.xml"),
        wav_policy=args.wav_policy or os.path.join(mc_dir, "WAV_policy.xml"),
        attachment_size=args.attachment_size,
    )

    if not os.path.exists(config.docs_dir):
        os.makedirs(config.docs_dir)
    if not os.path.exists(config.mkv_out_dir):
        os.makedirs(config.mkv_out_dir, exist_ok=True)
    check_dependencies()

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
