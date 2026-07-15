# srw Manual

**SMPL RAWcooked Workflow** — Stanford Media Preservation Lab

---

## Overview

`srw` batch-processes folders of DPX image sequences (with optional WAV audio) into lossless FFv1/Matroska (MKV) files for long-term preservation in an OAIS digital repository. Each sequence is run through a Step 0 pre-flight check followed by ten numbered steps, every step is logged with a resumable success marker, and the tool can be safely re-run after any failure or interruption. `--start-step`/`--end-step` can also target a specific range on demand — see [Running a specific step range](#running-a-specific-step-range-start-step--end-step).

---

## Directory structure

```
project-directory/
├── Source/
│   └── sequence_name/
│       ├── sequence_name_00001_pm.dpx
│       ├── sequence_name_00001_pm.dpx.md5    # Sidecar checksums (relocated after verification, step 2)
│       ├── ...
│       └── sequence_name_pm.wav              # Optional audio
├── Documents/
│   ├── sequence_name.xml                     # Required: custom Matroska metadata tags
│   ├── sequence_name_process.log             # Generated: timestamped workflow log
│   ├── sequence_name_inventory.csv           # Generated: file inventory
│   └── sequence_name_source_md5_sidecars/    # Generated: original sidecar .md5s, relocated (not deleted) in step 2
└── MediaConch/
    ├── DPX_SMPTE-CORE.xml                    # Required: DPX validation policy
    └── WAV_policy.xml                        # Required: WAV validation policy
```

`srw` does not derive these paths from its own install location — each is a CLI flag (see below), so a single `srw` install can be pointed at any project directory or drive. `srw run` processes one such directory set; `srw batch` (see [below](#batch-mode)) launches several at once.

---

## CLI flags (`srw run`)

| Flag | Default | Purpose |
|------|---------|---------|
| `--source-dir` | `./Source` | Parent directory containing one subfolder per DPX sequence |
| `--output-dir` | *(required)* | Destination for MKV, RAWcooked log, checksum, and MP4 review files — typically an external or secondary drive |
| `--docs-dir` | `./Documents` | Per-sequence process logs, inventory CSVs, and required XML metadata |
| `--mediaconch-dir` | `./MediaConch` | Directory containing MediaConch policy XML files |
| `--dpx-policy` | `<mediaconch-dir>/DPX_SMPTE-CORE.xml` | DPX MediaConch policy path |
| `--wav-policy` | `<mediaconch-dir>/WAV_policy.xml` | WAV MediaConch policy path |
| `--attachment-size` | `5000000` (5 MB) | RAWcooked attachment size limit in bytes, passed as `-s` |
| `--mediaconch-batch-size` | `2000` | Files validated per MediaConch invocation (steps 3–4), instead of one process per file |
| `--start-step` | `1` | Skip steps before this one, treating them as already complete (see [below](#running-a-specific-step-range-start-step--end-step)) |
| `--end-step` | `10` | Stop after this step instead of continuing to the end (see [below](#running-a-specific-step-range-start-step--end-step)) |
| `--skip-preflight` | *(off)* | Skip the Step 0 pre-flight check |
| `--disk-space-margin` | *(estimated)* | Required free space on `--output-dir` as a multiple of total source size — overrides the default estimate if set (see [Disk space check](#disk-space-check---disk-space-margin)) |

`--output-dir` is the flag that typically changes between runs/deployments — point it at whichever drive is receiving that batch's output.

---

## Step 0: Pre-flight Check

Runs before Step 1 (unless `--skip-preflight` is set) and reports on sequence composition without modifying anything:

- **DPX frame count** and a **best-effort frame-numbering gap scan** — takes the last run of digits in each `.dpx` filename as its frame number and reports any integers missing from the min–max range. This is a heuristic (it assumes the frame number is the last numeric run in the filename) and is logged as a warning only; it doesn't halt the run.
- **Audio presence (Y/N)** — whether any `.wav` files exist in the source folder, and how many.
- **Disk space on `--output-dir`** — **halts the run** if there isn't enough free space. See [Disk space check](#disk-space-check---disk-space-margin) below.
- **Sidecar `.md5` coverage** — counts source files missing a sidecar checksum and logs a warning if any are found. This is informational only; Step 2 still halts with full per-file detail if a sidecar is actually missing when it runs.
- **Metadata XML presence** (`{docs-dir}/{sequence_name}.xml`) — **halts the run** if missing. This file isn't otherwise required until Step 7, by which point Step 6's RAWcooked transcode (often the most time-consuming step) has already completed; checking for it here surfaces the problem before that time is spent.
- **MediaConch policy file presence** (`--dpx-policy`, and `--wav-policy` if audio was detected) — **halts the run** if the configured policy path doesn't exist. Previously this only surfaced as a `mediaconch` invocation failing against a nonexistent path in Steps 3/4.

Pre-flight failures return the same halt-and-resume behavior as any other step: fix the issue and re-run.

### Disk space check (`--disk-space-margin`)

Before any processing starts, `srw` checks free space on the filesystem containing `--output-dir` against the sequence's total source (DPX+WAV) size. FFv1 lossless compression isn't predictable upfront from a fixed ratio — a clean B&W scan can end up well under 50% of its DPX size, while a grainy color negative might barely compress at all — so instead of assuming one number for all content, `srw` **estimates the required margin from this deployment's own history**:

- It looks at every sequence in the same `--docs-dir` that fully completed (reached Step 10), reads the source size it recorded at Step 1 and the actual finished `.mkv` size, and computes the ratio actually achieved.
- It uses the **worst** (least-compressed) ratio seen so far, plus a small safety pad (5%), as the required-space multiplier for the sequence about to run.
- If there's no completed sequence yet in this `--docs-dir` — a fresh deployment, or the very first reel — it falls back to an assumed ~1:1 ratio (a 10% pad on top of the full source size) as a conservative starting point, since there's no real data yet to estimate from.

In practice this means the check gets *less* conservative over time as it learns your actual footage's compression behavior, rather than staying pinned to a one-size-fits-all guess: after your first B&W reel finishes, later pre-flight checks in that same deployment will require space closer to the real ~40–50% ratio instead of assuming worst case. The pre-flight log line shows which basis was used, e.g.:

```
Output drive free space: 1.8 TB (need ~450.3 GB at 0.45x source size, estimated from prior sequences in this deployment)
```

Running out of disk space partway through Step 6 — often the most time-consuming step, potentially hours into an encode — is a worse failure than a false-positive halt here, so a fresh deployment's fallback errs toward flagging early rather than assuming compression it hasn't observed yet. Set `--disk-space-margin` explicitly to bypass estimation entirely and use a fixed value instead:

```bash
srw run --output-dir /media/smpl-5220r/A/MKV --disk-space-margin 0.6
```

---

## Running a specific step range (`--start-step` / `--end-step`)

By default `srw run` executes the full pre-flight-through-Step-10 workflow. `--start-step N` and `--end-step N` (both `1`–`10`, default `1` and `10`) narrow this to a specific range for two situations:

**Resuming without a process log.** The normal resume system reads `>>> SUCCESS: Step N` markers from the process log to know what's already done. If that log is unavailable — lost to a drive failure, or the job ran to a certain point on a different machine — but you're already certain earlier steps passed (e.g. you've independently confirmed the MD5s and MediaConch validation), `--start-step N` marks steps `1` through `N-1` complete without re-verifying them, so the run picks up at step `N`.

```bash
srw run --output-dir /media/smpl-5220r/A/MKV --start-step 6   # skip 1-5, start at RAWcooked transcode
```

> **Warning:** Step 2 relocates each source file's sidecar `.md5` out of the source folder before Step 6 runs (see [below](#why-sidecar-md5s-are-relocated-not-deleted)) — RAWcooked's `--all` flag embeds any stray file it finds in the source folder as an MKV attachment. Skipping Step 2 via `--start-step` does **not** perform that relocation. If you use `--start-step` to jump past Step 2, make sure the source folder is already clear of sidecars (or is fine having them embedded) before Step 6 runs.

**Running an isolated step, or a few, on their own.** `--start-step` and `--end-step` combined run just that range and stop — useful for validating a sequence against a MediaConch policy without doing anything else:

```bash
srw run --output-dir /media/smpl-5220r/A/MKV --start-step 3 --end-step 4   # MediaConch validation only
```

Only the external tools actually used within the given range are required to be on `PATH` — running steps 3–4 alone doesn't require `rawcooked`, `mkvpropedit`, `ffmpeg`, or `mediainfo` to be installed. When `--end-step` stops a run short, `srw` reports it as a successful invocation of the requested range (not a failure) — the sequence isn't marked fully processed, and a later run with a wider (or default) range picks up wherever the log's markers leave off.

---

## Ten-step workflow

### Step 1: Inventory Generation
Walks the source directory and writes a CSV listing all non-hidden files with path, filename, extension, and size. Per-file sizes in the CSV are always in MB; the summary line logged for the step switches from MB to GB once the sequence's total size reaches 1 GiB (e.g. `Inventory complete. Found 28114 DPX files. Total: 1025.7 GB`), since large sequences reported in MB are unreadable.
- **Output:** `{docs-dir}/{sequence_name}_inventory.csv`

### Step 2: Checksum Verification
Verifies each source file against its sidecar `.md5` checksum file. Halts immediately on mismatch. Once verified, each sidecar is *relocated* (not deleted) to `{docs-dir}/{sequence_name}_source_md5_sidecars/`, preserving the source folder's relative layout — see [Why sidecar MD5s are relocated, not deleted](#why-sidecar-md5s-are-relocated-not-deleted) below. If interrupted partway through, re-running this step skips files whose sidecar has already been relocated rather than reporting them as missing.
- **Output:** `{docs-dir}/{sequence_name}_source_md5_sidecars/` (relocated originals)

### Step 3: MediaConch DPX Validation
Validates every `.dpx` file against the DPX policy. Checks format, magic number, bit depth, endianness, compression, color space, SMPTE header fields. Halts on first invalid file (in original sorted order) and logs its full policy output.

Files are validated in batches of `--mediaconch-batch-size` (default 2000) per MediaConch invocation, not one process per file — DPX sequences routinely run to tens or hundreds of thousands of frames, and one `mediaconch` (plus historically an extra shell + `mktemp`) process per file doesn't scale. Each batch call uses MediaConch's CSV output (`-fc`) to get a fast pass/fail per file; only the specific file that fails gets a second, single-file invocation to capture the full human-readable policy detail for the log — everything else is exactly as verbose as before, just far fewer processes spawned to get there. See [Why MediaConch validation is batched](#why-mediaconch-validation-is-batched-not-one-process-per-file) below.

### Step 4: MediaConch WAV Validation
Validates any `.wav` files against the WAV policy (same batching mechanism as step 3, though batch size rarely matters here since sequences typically have only one or a few WAV files). If no WAV files are found, logs "No .wav files found (picture only)" and marks the step complete. Halts on invalid audio.

### Step 5: Manifest Generation
Creates an MD5 checksum manifest of all source files. **Critically, this is written into the source DPX folder** (not `--docs-dir`), named after the expected MKV output (e.g., `sequence_name.md5`). This placement causes RAWcooked to embed it natively as an attachment during encoding via `--all`.
- **Output:** `{source-dir}/{sequence_name}/{sequence_name}.md5`

### Step 6: RAWcooked Transcode
Encodes the DPX sequence (and WAV if present) to FFv1/MKV. After encoding, RAWcooked performs an automatic reversibility check.

```bash
rawcooked -y --all -s {attachment-size} --log-name '{rc_log_path}' '{source_folder_path}' -o '{mkv_path}'
```

The tool tracks three phases in the log: DPX File Analysis, Lossless Encoding, and Reversibility Check. Once RAWcooked finishes, `srw` prepends a short identifying header to the top of the `.log` file itself:

```
============================================================
Stanford Media Preservation Lab
RAWcooked processing completed 26/07/15, 03:45 PM
============================================================

<RAWcooked's own log output follows>
```

This only happens the first time Step 6 actually completes — re-running an already-completed sequence (resume, or a later re-scan for bypassed prompts) doesn't prepend the header again. After completion, `srw` also scans the RAWcooked log for warnings about bypassed prompts or reversibility failures.
- **Output:** `{output-dir}/{sequence_name}.mkv`, `{output-dir}/{sequence_name}.log`

### Step 7: Embed Metadata Tags
Embeds custom Matroska tags from the XML file into the MKV using `mkvpropedit`:

```bash
mkvpropedit '{mkv_path}' --tags all:'{custom_xml}'
```
- **Requires:** `{docs-dir}/{sequence_name}.xml`

### Step 8: Generate Review Derivative
Creates an H.264/MP4 proxy for visual QC:

```bash
ffmpeg -i '{mkv_path}' -crf 18 -vf 'scale=-2:720' -pix_fmt yuv420p '{mp4_out}'
```
- **Output:** `{output-dir}/{sequence_name}_rawcooked_review.mp4`

### Step 9: MediaInfo Technical Metadata
Runs `mediainfo -f -i` against the finished MKV and writes the full technical metadata dump to a text file in `--docs-dir`, for QC review and database ingest. The same output is also appended to the process log.

```bash
mediainfo -f -i '{mkv_path}'
```
- **Output:** `{docs-dir}/{sequence_name}_mediainfo.txt`

### Step 10: Final Deliverable Hashes
Generates MD5 checksums for both the MKV and the RAWcooked log file.
- **Output:** `{output-dir}/{sequence_name}.mkv.md5`, `{output-dir}/{sequence_name}.log.md5`

---

## Process log

Each sequence produces a detailed timestamped log at `{docs-dir}/{sequence_name}_process.log`:

- Every step writes a `>>> SUCCESS: Step N completed successfully.` marker (used by the resume system)
- Each step is separated by a blank line for readability
- A process summary block is written between steps 9 and 10, showing each step's name with its **original** completion timestamp (not the current time)
- On resumed runs, skipped steps pull their original timestamps from the log
- If the tool is re-run after a failure, a `SCRIPT RESUMED` header is appended

### Example process summary block

```
[2026-04-13 12:23:00]
============================================================
[2026-04-13 12:23:00]  PROCESS SUMMARY
[2026-04-13 12:23:00] ============================================================
[2026-04-13 11:54:14]  STEP: 1. INVENTORY GENERATION - completed successfully
[2026-04-13 11:54:42]  STEP: 2. CHECKSUM VERIFICATION - completed successfully
[2026-04-13 11:58:20]  STEP: 3. MEDIACONCH DPX VALIDATION - completed successfully
[2026-04-13 11:58:21]  STEP: 4. MEDIACONCH WAV VALIDATION - completed successfully
[2026-04-13 12:14:14]  STEP: 5. MANIFEST GENERATION (.md5) - completed successfully
[2026-04-13 12:15:03]  STEP: 6. RAWCOOKED TRANSCODE - completed successfully
[2026-04-13 12:20:18]  STEP: 7. EMBED METADATA TAGS - completed successfully
[2026-04-13 12:22:50]  STEP: 8. GENERATE REVIEW DERIVATIVE - completed successfully
[2026-04-13 12:22:58]  STEP: 9. MEDIAINFO TECHNICAL METADATA - completed successfully
[2026-04-13 12:23:00] ** All processes completed successfully **
[2026-04-13 12:23:00] ** Starting md5 checksum generation **
[2026-04-13 12:23:00] ============================================================
```

---

## Resume system

`srw` is fully resumable. If any step fails or the process is interrupted:

1. Re-run `srw` with the same flags
2. It reads the process log for `>>> SUCCESS: Step N` markers
3. Completed steps are skipped; processing resumes from the failure point

Batch processing itself is sequential: `srw` halts at the first sequence that errors rather than continuing on to the next one, so a single bad sequence doesn't leave later ones silently unprocessed. Re-running after fixing the issue picks up where it left off.

---

## MediaConch policies

`--mediaconch-dir` must contain the two policy files referenced by `--dpx-policy`/`--wav-policy` (`DPX_SMPTE-CORE.xml` and `WAV_policy.xml` by default). These aren't bundled with `srw` — they're lab-specific and should be sourced from your MediaConch installation or exported from the MediaConch GUI, then committed to your own deployment's `MediaConch/` directory.

MediaConch validates file *contents*, not extensions — a JPEG renamed with a `.dpx` extension fails validation with detailed policy output logged. Files missing from the source directory simply don't appear in the validation log (glob won't find what isn't there) — the Step 0 pre-flight check's best-effort frame-numbering gap scan (see [above](#step-0-pre-flight-check)) is a separate, earlier check for exactly this.

WAV validation is optional by design: many film scans are picture-only. If no `.wav` files are present, step 4 logs "No .wav files found (picture only)" and marks the step complete.

---

## Key design decisions

### Why the MD5 manifest goes in the source folder (step 5)
So that `rawcooked --all` picks it up and embeds it natively as a Matroska attachment — this is the correct way to include the manifest inside the MKV, with RAWcooked handling the attachment itself.

### Why there is no post-encode attachment step
An earlier design embedded the RAWcooked `.log` and `.md5` manifest into the MKV after encoding via `mkvpropedit --add-attachment`. This was removed for two reasons:

1. **It broke RAWcooked reversibility.** Adding attachments via `mkvpropedit` after the encode places them before the reversibility data in the attachment block. RAWcooked expects its reversibility data to be in the **last** attachment position — once other attachments are appended after it, `rawcooked --all` on the MKV returns "No reversibility data found."
2. **Archival clarity.** A human-readable `.log` file visible as a sidecar is more accessible over a multi-decade horizon than something embedded in the container — no specialized tooling is needed to confirm RAWcooked was used.

This RAWcooked attachment-position dependency has been reported to MediaArea via GitHub.

### Why the RAWcooked log goes to `--output-dir`
The `.log` file is generated by RAWcooked during encoding (`--log-name`), so it doesn't exist until the encode completes — it can't be pre-placed in the source directory for native embedding. It's output as a sidecar alongside the MKV, and its MD5 checksum is generated in step 9.

### The `--attachment-size` flag
RAWcooked's own default attachment size limit is 1 MB. The MD5 manifest for a long DPX sequence can exceed this — roughly 65 bytes per line, so a 44,420-frame sequence produces a manifest of ~2.6 MB. `srw` defaults `--attachment-size` to 5,000,000 (5 MB), accommodating sequences up to roughly 77,000 frames; raise it further for longer sequences.

### Why sidecar MD5s are relocated, not deleted
An earlier version deleted each source file's sidecar `.md5` immediately after step 2 verified it. That deletion was actually necessary for something else: `rawcooked --all` in step 6 embeds *any* stray files it finds in the source folder as MKV attachments (the same mechanism that embeds step 5's manifest) — if the original per-file sidecars were still present, RAWcooked would try to embed each one individually, potentially hundreds of thousands of tiny attachments, likely breaking the "reversibility data must be the last attachment" constraint (see [below](#rawcooked-reversibility-data-must-be-the-last-attachment)).

But deleting them outright also throws away the only baseline for re-verifying the original transfer's integrity later — if the tool is interrupted or halted anywhere in steps 3-9 and re-run from scratch, or if someone wants to confirm months later that the DPX files haven't silently changed since ingest, there's nothing left to check against. So step 2 now *relocates* each sidecar out of the source folder (satisfying RAWcooked's constraint) into `{docs-dir}/{sequence_name}_source_md5_sidecars/` instead of deleting it, and keeps it there permanently — the storage cost is negligible (tens of bytes per file) next to the actual DPX/MKV data.

### Why MediaConch validation is batched, not one process per file
An earlier version ran `mediaconch` once per DPX file — worse, via a `HOME=$(mktemp -d) mediaconch ...` shell string, which spawned a bash shell *and* a `mktemp` process in addition to `mediaconch` itself for every single file, and never cleaned up the temp directory `mktemp -d` created. For a 100,000-frame sequence that's 300,000 process spawns and 100,000 leaked directories.

MediaConch's CLI natively accepts a file list in one invocation (`mediaconch -p policy file1 file2 ...`) and, with `-fc` (CSV format), returns one row per file with a clean pass/fail column — so `srw` now validates files in batches of `--mediaconch-batch-size` (default 2000; chunked mainly to stay well under the OS's `ARG_MAX` for a single exec, not because MediaConch needs it). The temp `HOME` MediaConch reads/writes config and cache under is still isolated per call (so concurrent `srw` invocations, e.g. under [batch mode](#batch-mode), don't collide on it), but it's created and cleaned up directly in Python (`tempfile.TemporaryDirectory`) instead of via a shell subprocess.

Halt-on-first-invalid-file semantics and the full human-readable policy detail in the log are unchanged: batches are only evaluated as far as the caller consumes them, so once an invalid file is found, no later batches run, and that one file gets a second, single-file `mediaconch` call to capture its full policy output for the log — exactly as verbose as before, just far fewer processes to get there.

---

## Known issues and investigations

### RAWcooked segfault on some AMD platforms (return code -11)

**Symptom:** RAWcooked can crash with return code -11 (SIGSEGV) during the reversibility check phase, both during encoding and during reversal. Observed occurring immediately at the start of the reversibility check (`Time=00:00:00`, 0%), after the encode itself completes successfully. Passes on retry.

**Investigation notes from an AMD Threadripper Pro 3955WX / Zen 2 system:**
- Thermals, ECC errors, and BIOS/memory settings were ruled out
- Peak RAM per RAWcooked job was well under available memory — memory pressure ruled out
- Valgrind showed zero memory errors but repeated large-region `mmap`/`munmap` cycles during the reversibility check, once per frame — the segfault did not reproduce under Valgrind, which serializes memory operations and may mask a race condition
- Not observed on a comparable Intel Xeon system running the same workload

**Current hypothesis:** a race condition or memory-mapping behavior in RAWcooked's reversibility check that manifests on some platforms but not others.

**Practical workaround:** `srw`'s resume system handles intermittent failures automatically — just re-run. If a given machine reproduces this reliably, prefer a different machine for reversal-heavy work until upstream resolves it.

### RAWcooked reversibility data must be the last attachment
See [Key design decisions](#why-there-is-no-post-encode-attachment-step) above. Confirmed by extracting and re-attaching the reversibility data in last position, which restored reversal functionality.

---

## Output files summary

For a sequence named `sequence_name`, after successful processing:

### In `--output-dir`
| File | Description |
|------|-------------|
| `sequence_name.mkv` | Lossless FFv1/Matroska with embedded reversibility data and MD5 manifest |
| `sequence_name.mkv.md5` | MD5 checksum of the MKV |
| `sequence_name.log` | RAWcooked process log (sidecar) |
| `sequence_name.log.md5` | MD5 checksum of the RAWcooked log |
| `sequence_name_rawcooked_review.mp4` | H.264 proxy (720p, CRF 18) |

### In `--docs-dir`
| File | Description |
|------|-------------|
| `sequence_name_process.log` | Timestamped srw workflow log |
| `sequence_name_inventory.csv` | Source file inventory with sizes |
| `sequence_name_source_md5_sidecars/` | Original per-file sidecar `.md5`s, relocated (not deleted) here in step 2 |
| `sequence_name_mediainfo.txt` | `mediainfo -f -i` technical metadata dump, for QC/database use (step 9) |

### Embedded in MKV
| Attachment | Description |
|------------|-------------|
| Reversibility data | Enables lossless reversal back to DPX via `rawcooked --all` |
| MD5 manifest | Checksum manifest of all source DPX/WAV files |

---

## Parallel processing notes

`srw run` processes sequences sequentially within a single invocation. For parallel processing, run separate invocations against separate `--source-dir`/`--docs-dir` pairs — see [Batch mode](#batch-mode) below for a way to launch several at once from one command. RAWcooked's reversibility check is I/O- and CPU-intensive; test how many concurrent jobs your storage and platform can sustain before scaling up — low CPU/RAM usage in `htop` doesn't rule out an I/O ceiling, since RAWcooked work is typically bottlenecked on storage throughput rather than compute. Watch `iostat -x 1` or `iotop` against the actual RAID/NVMe devices while increasing concurrency, and stop once a device's `%util` saturates or errors start appearing — an unstable platform may also show the [segfault above](#rawcooked-segfault-on-some-amd-platforms-return-code--11) under load that a single job does not.

---

## Batch mode

`srw batch` launches several `srw run` invocations in parallel from one command, for deployments where multiple DPX batches are staged at once — for example, several batch folders spread across multiple RAID volumes, each writing its own output to a separate destination drive.

### Config format

```toml
# batches.toml
[[batch]]
batch_dir = "/mnt/raid1/batch1"
output_dir = "/mnt/nvme_a/mkv"

[[batch]]
batch_dir = "/mnt/raid1/batch2"
output_dir = "/mnt/nvme_a/mkv"

[[batch]]
batch_dir = "/mnt/raid1/batch3"
output_dir = "/mnt/nvme_a/mkv"

[[batch]]
batch_dir = "/mnt/raid2/batch1"
output_dir = "/mnt/nvme_b/mkv"

[[batch]]
batch_dir = "/mnt/raid2/batch2"
output_dir = "/mnt/nvme_b/mkv"

[[batch]]
batch_dir = "/mnt/raid2/batch3"
output_dir = "/mnt/nvme_b/mkv"
```

Each `[[batch]]` entry needs at minimum `batch_dir` and `output_dir`. `source_dir`/`docs_dir`/`mediaconch_dir` default to `{batch_dir}/Source`, `{batch_dir}/Documents`, `{batch_dir}/MediaConch` — override any of them individually if a deployment's layout differs:

```toml
[[batch]]
batch_dir = "/mnt/raid1/batch1"
source_dir = "/mnt/raid1/batch1/CustomSource"   # overrides the Source/ default
output_dir = "/mnt/nvme_a/mkv"
attachment_size = 8000000                        # overrides the srw run default for this batch only
start_step = 3                                   # forwarded as --start-step to this batch's srw run
end_step = 4                                      # forwarded as --end-step
skip_preflight = true                            # forwarded as --skip-preflight
```

An optional `name` sets the label used for that job's log file and console output; if omitted, it's derived from `batch_dir`'s parent and basename (e.g. `/mnt/raid1/batch1` → `raid1_batch1`) — using the basename alone would collide whenever the same `batch1`/`batch2`/`batch3` naming repeats across multiple RAID volumes, which is the expected layout here. Names must be unique across the config; a collision (e.g. two entries both resolving to the same explicit `name`) is rejected before anything launches.

### Running it

```bash
srw batch --config batches.toml
```

| Flag | Default | Purpose |
|------|---------|---------|
| `--config` | *(required)* | Path to the TOML config |
| `--log-dir` | `./srw-batch-logs` | Directory to receive each job's full `srw run` output, one `{name}.log` file per batch |
| `--max-parallel` | *(all configured batches at once)* | Cap on concurrent jobs |

There's no built-in default concurrency cap — every batch entry launches at once unless `--max-parallel` says otherwise. The real per-machine ceiling for parallel RAWcooked jobs is I/O-bound and platform-specific (see [Parallel processing notes](#parallel-processing-notes)); measure it empirically rather than assuming a number from CPU core count.

### Output and failure handling

Each job's full console output (identical to what `srw run` would print directly) is captured to `{log-dir}/{name}.log`; the orchestrator's own terminal output is just a concise per-job status line plus a final summary. If a job fails, `srw batch` reports it in the summary and exits non-zero, but does **not** stop the other jobs — each one runs to completion or failure independently. Fix the underlying issue and re-run `srw batch` with the same config: `srw run`'s own step-level resume system means already-completed steps within each job are skipped, exactly as if you'd re-run that one job by hand.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `FATAL ERROR: mkvpropedit not installed` | `mkvtoolnix` missing | `sudo apt install mkvtoolnix` |
| `FATAL ERROR: rawcooked not installed` | `rawcooked` missing | `sudo apt install rawcooked` |
| `FATAL ERROR: mediaconch not installed` | `mediaconch` missing | `sudo apt install mediaconch` |
| `FATAL ERROR: mediainfo not installed` | `mediainfo` missing | `sudo apt install mediainfo` |
| Pre-flight: `MISSING: metadata XML not found` | No metadata XML in `--docs-dir` | Add `{docs-dir}/{sequence_name}.xml` before re-running |
| Pre-flight: `MISSING: DPX/WAV MediaConch policy not found` | Configured policy path doesn't exist | Check `--dpx-policy`/`--wav-policy` (or `--mediaconch-dir`) point at real files |
| Pre-flight: `INSUFFICIENT DISK SPACE` | Not enough free space on `--output-dir` for the source size times the (estimated or explicit) margin | Free up space, point `--output-dir` at a drive with more room, or set `--disk-space-margin` explicitly if the estimate is a false positive |
| `CRITICAL ERROR: Missing MD5 for {file}` | Sidecar `.md5` missing for a source file | Add the missing sidecar checksum before re-running |
| `[INVALID] {file}` in step 3/4 | File fails MediaConch policy | Check the logged policy output; the file's actual content (not its extension) failed a rule |
| `CRITICAL: Missing '{sequence_name}.xml'` | No metadata XML in `--docs-dir` | Add `{docs-dir}/{sequence_name}.xml` before re-running (pre-flight normally catches this first) |
| Return code -11 during step 6 | See [Known issues](#known-issues-and-investigations) | Re-run; the resume system will retry step 6 |
