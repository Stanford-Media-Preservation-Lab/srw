#!/usr/bin/env python3
"""
run_srw.py -- per-batch-folder launcher for srw (SMPL RAWcooked Workflow).
Stanford Media Preservation Lab

Copy this file into a batch folder (the one holding Source/, Documents/ and
MediaConch/), edit the two settings below if needed, then run it from there:

    cd "/media/smpl-5220r/RAID 1/RAWcooked_1/batch_1"
    ./run_srw.py run --output-dir /media/smpl-5220r/A/MKV

It takes exactly the same arguments as `srw` (`run --help`, `--version`, ...).
srw itself is NOT installed: this launcher imports it straight from SRW_REPO.
"""

import os
import sys

# ============================ EDIT THESE ============================

# Where the srw repo is cloned (the folder that contains the `srw/` package).
SRW_REPO = os.path.expanduser("~/srw")

# Steps to SKIP for runs started from THIS batch folder, e.g. [3].
# Leave as [] for normal operation. Skipping steps is not a normal operation:
#   * Skipping Step 2 leaves sidecar .md5 files in the source folder, which
#     breaks `rawcooked --all` in Step 6.
#   * Skipping Step 5 leaves the MKV without its conformance manifest.
# A skipped step is recorded in the sequence's process log and is NOT retried
# on later runs (see MANUAL.md, "Skipping steps"). Set this back to [] when done.
SKIP_STEPS = []

# ====================================================================


def _prepare(srw_repo, skip_steps, argv):
    """Make srw importable from srw_repo and hand it the skip list.
    Exits with status 2 and a message on any problem."""
    if not os.path.isfile(os.path.join(srw_repo, "srw", "cli.py")):
        sys.exit(f"run_srw.py: no srw package found in SRW_REPO = {srw_repo!r}. "
                 "Edit SRW_REPO at the top of this file.")
    bad = [s for s in skip_steps if not isinstance(s, int) or isinstance(s, bool) or not 1 <= s <= 10]
    if bad:
        sys.exit(f"run_srw.py: SKIP_STEPS may only contain step numbers 1-10 (got {bad}).")
    if skip_steps and argv and argv[0] == "batch":
        sys.exit("run_srw.py: refusing to run `batch` while SKIP_STEPS is set -- a skip is "
                 "per batch folder and `srw batch` jobs would not receive it. "
                 "Set SKIP_STEPS = [] or run `run` from the batch folder.")

    sys.path.insert(0, srw_repo)
    os.environ["PYTHONPATH"] = os.pathsep.join(
        p for p in (srw_repo, os.environ.get("PYTHONPATH", "")) if p)

    import srw.cli
    srw.cli.SKIP_STEPS = sorted(set(skip_steps))


def main():
    _prepare(SRW_REPO, SKIP_STEPS, sys.argv[1:])
    from srw.cli import main as srw_main
    sys.argv[0] = "srw"
    srw_main()


if __name__ == "__main__":
    main()
