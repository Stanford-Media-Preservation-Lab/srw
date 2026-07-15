"""
srw.batch — orchestrator for running multiple `srw run` invocations in parallel.

Built for deployments with more than one batch of DPX sequences staged at once
(e.g. several source folders spread across multiple RAID volumes, each writing
its output to a separate destination drive). Each entry in the TOML config is
launched as its own `srw run` subprocess; srw's own per-sequence resumability
is unchanged, so a failed batch can be fixed and the whole `srw batch` command
re-run without repeating completed work.

No concurrency limit is imposed here — this lab's actual per-machine ceiling
(I/O-bound, not CPU/RAM-bound) hasn't been established yet, so `--max-parallel`
defaults to running every configured batch at once. Use `--max-parallel` to
throttle down once real testing (iostat/iotop against the RAID and NVMe
devices, not just htop) has established one.
"""

import argparse
import os
import sys
import subprocess
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class BatchJob:
    name: str
    source_dir: str
    docs_dir: str
    mediaconch_dir: str
    output_dir: str
    attachment_size: Optional[int] = None
    start_step: Optional[int] = None
    end_step: Optional[int] = None
    skip_preflight: bool = False
    disk_space_margin: Optional[float] = None


def _default_job_name(batch_dir, index):
    """Derives a default job name from batch_dir's parent + basename, e.g.
    /mnt/raid1/batch1 -> "raid1_batch1". Using the basename alone would collide
    whenever the same batch-folder naming (batch1, batch2, ...) repeats across
    multiple RAID volumes, which is the expected layout this orchestrator targets."""
    if not batch_dir:
        return f"batch{index}"
    normalized = os.path.normpath(batch_dir)
    parent_name = os.path.basename(os.path.dirname(normalized))
    base_name = os.path.basename(normalized)
    return f"{parent_name}_{base_name}" if parent_name else base_name


def load_batch_config(config_path):
    """Parses a TOML batch config into a list of BatchJob.

    Each [[batch]] table needs at minimum `batch_dir` and `output_dir`.
    `source_dir`/`docs_dir`/`mediaconch_dir` default to `batch_dir`/Source,
    `batch_dir`/Documents, `batch_dir`/MediaConch respectively, and can be
    overridden individually if a deployment's layout differs.
    """
    with open(config_path, "rb") as f:
        data = tomllib.load(f)

    entries = data.get("batch", [])
    if not entries:
        raise ValueError(f"No [[batch]] entries found in {config_path}")

    jobs = []
    for i, entry in enumerate(entries):
        if "output_dir" not in entry:
            raise ValueError(f"batch entry {i} is missing required key 'output_dir'")
        batch_dir = entry.get("batch_dir")
        if not batch_dir and "source_dir" not in entry:
            raise ValueError(f"batch entry {i} needs either 'batch_dir' or 'source_dir'")

        name = entry.get("name") or _default_job_name(batch_dir, i)
        source_dir = entry.get("source_dir") or os.path.join(batch_dir, "Source")
        docs_dir = entry.get("docs_dir") or os.path.join(batch_dir, "Documents")
        mediaconch_dir = entry.get("mediaconch_dir") or os.path.join(batch_dir, "MediaConch")

        jobs.append(BatchJob(
            name=name,
            source_dir=source_dir,
            docs_dir=docs_dir,
            mediaconch_dir=mediaconch_dir,
            output_dir=entry["output_dir"],
            attachment_size=entry.get("attachment_size"),
            start_step=entry.get("start_step"),
            end_step=entry.get("end_step"),
            skip_preflight=entry.get("skip_preflight", False),
            disk_space_margin=entry.get("disk_space_margin"),
        ))

    names = [job.name for job in jobs]
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        raise ValueError(f"duplicate batch names in {config_path}: {sorted(duplicates)} — set an explicit 'name' per entry")

    return jobs


def _job_command(job: BatchJob):
    cmd = [
        sys.executable, "-m", "srw", "run",
        "--source-dir", job.source_dir,
        "--output-dir", job.output_dir,
        "--docs-dir", job.docs_dir,
        "--mediaconch-dir", job.mediaconch_dir,
    ]
    if job.attachment_size is not None:
        cmd += ["--attachment-size", str(job.attachment_size)]
    if job.start_step is not None:
        cmd += ["--start-step", str(job.start_step)]
    if job.end_step is not None:
        cmd += ["--end-step", str(job.end_step)]
    if job.skip_preflight:
        cmd += ["--skip-preflight"]
    if job.disk_space_margin is not None:
        cmd += ["--disk-space-margin", str(job.disk_space_margin)]
    return cmd


def _run_job(job: BatchJob, log_dir: str):
    log_path = os.path.join(log_dir, f"{job.name}.log")
    start = datetime.now()
    with open(log_path, "w") as log_file:
        log_file.write(f"srw batch — launching job '{job.name}' at {start.strftime('%Y-%m-%d %H:%M:%S')}\n")
        log_file.write(f"command: {' '.join(_job_command(job))}\n\n")
        log_file.flush()
        process = subprocess.run(_job_command(job), stdout=log_file, stderr=subprocess.STDOUT)
    elapsed = datetime.now() - start
    return job, process.returncode, elapsed, log_path


BATCH_EPILOG = """\
Config format (TOML):

  [[batch]]
  batch_dir = "/mnt/raid1/batch1"    # expects Source/, Documents/, MediaConch/ inside
  output_dir = "/mnt/nvme_a/mkv"

  [[batch]]
  batch_dir = "/mnt/raid1/batch2"
  output_dir = "/mnt/nvme_a/mkv"

Each [[batch]] entry needs at minimum batch_dir and output_dir. source_dir /
docs_dir / mediaconch_dir default to {batch_dir}/Source, /Documents,
/MediaConch -- override individually if a deployment's layout differs.
Optional per-entry overrides: name, attachment_size, start_step, end_step,
skip_preflight, disk_space_margin.

No concurrency cap by default -- every configured batch launches at once
unless --max-parallel limits it. The real per-machine ceiling for parallel
RAWcooked jobs is I/O-bound; measure with iostat/iotop against your actual
RAID/NVMe devices rather than assuming a number from CPU core count.

Example:
  srw batch --config batches.toml --max-parallel 3

Full reference: MANUAL.md#batch-mode in https://github.com/michaelangeletti/srw
"""


def add_batch_arguments(parser):
    parser.epilog = BATCH_EPILOG
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    parser.add_argument(
        "--config", required=True,
        help="Path to a TOML file with one or more [[batch]] entries (see MANUAL.md)",
    )
    parser.add_argument(
        "--log-dir", default="srw-batch-logs",
        help="Directory to write each batch job's full srw output to (default: ./srw-batch-logs)",
    )
    parser.add_argument(
        "--max-parallel", type=int, default=None,
        help="Maximum number of batch jobs to run at once (default: all configured batches at once)",
    )
    return parser


def batch_main(args):
    try:
        jobs = load_batch_config(args.config)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as e:
        print(f"FATAL ERROR: {e}")
        return 1

    os.makedirs(args.log_dir, exist_ok=True)
    max_workers = args.max_parallel or len(jobs)

    print("=" * 60)
    print(f"srw batch — launching {len(jobs)} job(s), up to {max_workers} in parallel")
    print(f"Per-job output: {args.log_dir}/<name>.log")
    print("=" * 60)
    for job in jobs:
        print(f"  {job.name}: {job.source_dir} -> {job.output_dir}")
    print()

    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_run_job, job, args.log_dir): job for job in jobs}
        for future in as_completed(futures):
            job, returncode, elapsed, log_path = future.result()
            results.append((job, returncode, elapsed))
            status = "OK" if returncode == 0 else "FAIL"
            print(f"[{status}] {job.name} ({elapsed}, exit {returncode}) — {log_path}")

    failed = [job for job, code, _ in results if code != 0]

    print("\n" + "=" * 60)
    print("BATCH ORCHESTRATOR SUMMARY")
    print("=" * 60)
    print(f"Total jobs: {len(jobs)}")
    print(f"Succeeded: {len(jobs) - len(failed)}")
    if failed:
        print(f"Failed: {len(failed)}")
        for job in failed:
            print(f"  [FAIL] {job.name} — see {args.log_dir}/{job.name}.log")
        print("\nFix the underlying issue and re-run `srw batch` with the same config;")
        print("completed steps within each job are skipped via srw's own resume system.")
    print("=" * 60 + "\n")

    return 1 if failed else 0
