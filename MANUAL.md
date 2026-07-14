# srw Manual

**SMPL RAWcooked Workflow** — Stanford Media Preservation Lab

---

## Overview

`srw` batch-processes folders of DPX image sequences (with optional WAV audio) into lossless FFv1/Matroska (MKV) files for long-term preservation in an OAIS digital repository. Each sequence is run through nine steps, every step is logged with a resumable success marker, and the tool can be safely re-run after any failure or interruption.

---

## Directory structure

```
project-directory/
├── Source/
│   └── sequence_name/
│       ├── sequence_name_00001_pm.dpx
│       ├── sequence_name_00001_pm.dpx.md5    # Sidecar checksums (deleted after verification)
│       ├── ...
│       └── sequence_name_pm.wav              # Optional audio
├── Documents/
│   ├── sequence_name.xml                     # Required: custom Matroska metadata tags
│   ├── sequence_name_process.log             # Generated: timestamped workflow log
│   └── sequence_name_inventory.csv           # Generated: file inventory
└── MediaConch/
    ├── DPX_SMPTE-CORE.xml                    # Required: DPX validation policy
    └── WAV_policy.xml                        # Required: WAV validation policy
```

`srw` does not derive these paths from its own install location — each is a CLI flag (see below), so a single `srw` install can be pointed at any project directory or drive.

---

## CLI flags

| Flag | Default | Purpose |
|------|---------|---------|
| `--source-dir` | `./Source` | Parent directory containing one subfolder per DPX sequence |
| `--output-dir` | *(required)* | Destination for MKV, RAWcooked log, checksum, and MP4 review files — typically an external or secondary drive |
| `--docs-dir` | `./Documents` | Per-sequence process logs, inventory CSVs, and required XML metadata |
| `--mediaconch-dir` | `./MediaConch` | Directory containing MediaConch policy XML files |
| `--dpx-policy` | `<mediaconch-dir>/DPX_SMPTE-CORE.xml` | DPX MediaConch policy path |
| `--wav-policy` | `<mediaconch-dir>/WAV_policy.xml` | WAV MediaConch policy path |
| `--attachment-size` | `5000000` (5 MB) | RAWcooked attachment size limit in bytes, passed as `-s` |

`--output-dir` is the flag that typically changes between runs/deployments — point it at whichever drive is receiving that batch's output.

---

## Nine-step workflow

### Step 1: Inventory Generation
Walks the source directory and writes a CSV listing all non-hidden files with path, filename, extension, and size.
- **Output:** `{docs-dir}/{sequence_name}_inventory.csv`

### Step 2: Checksum Verification
Verifies each source file against its sidecar `.md5` checksum file. Halts immediately on mismatch. Deletes all sidecar `.md5` files after successful verification.

### Step 3: MediaConch DPX Validation
Validates every `.dpx` file against the DPX policy. Checks format, magic number, bit depth, endianness, compression, color space, SMPTE header fields. Halts on first invalid file and logs full policy output.

### Step 4: MediaConch WAV Validation
Validates any `.wav` files against the WAV policy. If no WAV files are found, logs "No .wav files found (picture only)" and marks the step complete. Halts on invalid audio.

### Step 5: Manifest Generation
Creates an MD5 checksum manifest of all source files. **Critically, this is written into the source DPX folder** (not `--docs-dir`), named after the expected MKV output (e.g., `sequence_name.md5`). This placement causes RAWcooked to embed it natively as an attachment during encoding via `--all`.
- **Output:** `{source-dir}/{sequence_name}/{sequence_name}.md5`

### Step 6: RAWcooked Transcode
Encodes the DPX sequence (and WAV if present) to FFv1/MKV. After encoding, RAWcooked performs an automatic reversibility check.

```bash
rawcooked -y --all -s {attachment-size} --log-name '{rc_log_path}' '{source_folder_path}' -o '{mkv_path}'
```

The tool tracks three phases in the log: DPX File Analysis, Lossless Encoding, and Reversibility Check. After completion, it scans the RAWcooked log for warnings about bypassed prompts or reversibility failures.
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

### Step 9: Final Deliverable Hashes
Generates MD5 checksums for both the MKV and the RAWcooked log file.
- **Output:** `{output-dir}/{sequence_name}.mkv.md5`, `{output-dir}/{sequence_name}.log.md5`

---

## Process log

Each sequence produces a detailed timestamped log at `{docs-dir}/{sequence_name}_process.log`:

- Every step writes a `>>> SUCCESS: Step N completed successfully.` marker (used by the resume system)
- Each step is separated by a blank line for readability
- A process summary block is written between steps 8 and 9, showing each step's name with its **original** completion timestamp (not the current time)
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

MediaConch validates file *contents*, not extensions — a JPEG renamed with a `.dpx` extension fails validation with detailed policy output logged. Files missing from the source directory simply don't appear in the validation log (glob won't find what isn't there), so check for frame-numbering gaps separately.

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

### Embedded in MKV
| Attachment | Description |
|------------|-------------|
| Reversibility data | Enables lossless reversal back to DPX via `rawcooked --all` |
| MD5 manifest | Checksum manifest of all source DPX/WAV files |

---

## Parallel processing notes

`srw` processes sequences sequentially within a single invocation. For parallel processing, run separate instances against separate `--source-dir`/`--docs-dir` pairs. RAWcooked's reversibility check is I/O- and CPU-intensive; test how many concurrent jobs your storage and platform can sustain before scaling up — an unstable platform may show the segfault above under load that a single job does not.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `FATAL ERROR: mkvpropedit not installed` | `mkvtoolnix` missing | `sudo apt install mkvtoolnix` |
| `FATAL ERROR: rawcooked not installed` | `rawcooked` missing | `sudo apt install rawcooked` |
| `FATAL ERROR: mediaconch not installed` | `mediaconch` missing | `sudo apt install mediaconch` |
| `CRITICAL ERROR: Missing MD5 for {file}` | Sidecar `.md5` missing for a source file | Add the missing sidecar checksum before re-running |
| `[INVALID] {file}` in step 3/4 | File fails MediaConch policy | Check the logged policy output; the file's actual content (not its extension) failed a rule |
| `CRITICAL: Missing '{sequence_name}.xml'` | No metadata XML in `--docs-dir` | Add `{docs-dir}/{sequence_name}.xml` before re-running |
| Return code -11 during step 6 | See [Known issues](#known-issues-and-investigations) | Re-run; the resume system will retry step 6 |
