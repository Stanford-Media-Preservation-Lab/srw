# srw — Claude Code Context

Context for continuing development on this project. For usage/install docs see [README.md](README.md), [MANUAL.md](MANUAL.md), [INSTALL_UBUNTU.md](INSTALL_UBUNTU.md).

## What this is

`srw` (SMPL RAWcooked Workflow, package `srw`) is a batch preservation transcoding tool for the Stanford Media Preservation Lab (SMPL), developed collaboratively with Claude over several months beginning January 2026. It takes folders of DPX image sequences (with optional WAV audio) from film scanning and packages them into lossless FFv1/Matroska (MKV) files for an OAIS digital repository, via a resumable nine-step workflow (inventory, checksum verification, MediaConch validation, MD5 manifest, RAWcooked transcode, metadata tagging, review derivative, final hashing). Michael is the primary developer/maintainer.

Ubuntu-only — its dependencies (`rawcooked`, `mediaconch`, `mkvtoolnix`) don't have a supported macOS path, unlike this lab's other pipx tools ([vdg](https://github.com/michaelangeletti/vdg)) which run cross-platform.

**Lab infrastructure:**
- Primary workstation: AMD Threadripper Pro 3955WX (16c/32t, Zen 2), 128 GB ECC DDR4-3200, ASUS WRX80 Pro, Ubuntu 24.04, multiple NVMe + RAID array.
- Secondary workstation: Dual Intel Xeon Gold 5220R (48c/96t total), 128 GB RAM, Ubuntu 24.04, RAID0 + NVMe storage.

## Current state

At **v1.0.0** — first packaged release. The original script (developed iteratively as `smpl-rawcooked_v3_batch1.py` / `SMPL_RAWcooked_v3_6_260116_Ubuntu24_04.py`) was refactored into an installable `srw` package (`srw/cli.py`) to match this lab's other pipx tools:

- Hardcoded per-deployment config variables (`BASE_DIR`, `MKV_OUT_DIR`, `DOCS_DIR`, `MC_DIR`, `MC_POLICY`, `WAV_POLICY`) were replaced with CLI flags (`--source-dir`, `--output-dir`, `--docs-dir`, `--mediaconch-dir`, `--dpx-policy`, `--wav-policy`, `--attachment-size`) — deployment no longer requires editing the installed script.
- All nine workflow steps, the resume system, and log format are behaviorally unchanged from the original script.
- **Note on `--attachment-size`:** the source script hardcoded `-s 2000000` (2 MB) inline with no named variable; the design doc this repo was seeded from described a `RC_ATTACHMENT_MAX_SIZE = 5000000` variable and justified 5 MB against a real 44,420-frame sequence's ~2.6 MB manifest. This repo took the documented 5 MB figure as the default since it's the better-reasoned value and is now user-configurable either way — flag this to Michael if the 2 MB figure was actually intentional.
- MediaConch policy XML files (`DPX_SMPTE-CORE.xml`, `WAV_policy.xml`) are lab-specific and were not available on the machine this repo was created from — `MediaConch/` ships empty (`.gitkeep` only). These need to be added from the lab's MediaConch installation/GUI export before the tool can pass steps 3–4.
- No test suite existed prior to packaging; `tests/test_srw.py` covers the pure/testable helpers (`verify_md5`, `is_step_complete`, `get_step_completion_timestamp`, `mark_step_complete`, config defaults). The step-orchestration logic (`process_sequence`) still shells out to `rawcooked`/`mediaconch`/`mkvpropedit`/`ffmpeg` and is not unit-tested — validate changes to it against a real (or synthetic) DPX sequence.

## Known issues and investigations

### RAWcooked segfault on Threadripper (return code -11)

**Symptom:** RAWcooked crashes with return code -11 (SIGSEGV) during the reversibility check phase, both during encoding and during reversal. Always at `Time=00:00:00 (0%)` — immediately at the start of the reversibility check, after the encode itself completes successfully. Passes on retry.

**Affected platform:** AMD Threadripper Pro 3955WX / ASUS WRX80 Pro / Ubuntu 24.04. **Not affected:** Dual Intel Xeon Gold 5220R / Ubuntu 24.04 — six parallel jobs completed without error on 661 GB of DPX sequences.

**Investigation results:**
- Thermals ruled out (Tctl max ~62°C under load); ECC errors ruled out (no MCE/EDAC in `dmesg`); BIOS current (v1801); RAM at native JEDEC 3200 MT/s.
- BIOS changes tested (IOMMU on, Core Performance Boost off, Memory Interleaving off) — no effect.
- Peak RAM per job ~1.7 GB — memory pressure ruled out.
- Valgrind: zero memory errors, but ~5000 warnings of repeated large-region `mmap`/`munmap` cycles on the same ~7.7 GB region, once per frame during the reversibility check. Valgrind serializes memory operations, which may be why the segfault doesn't reproduce under it.

**Hypothesis:** race condition or memory-mapping behavior in RAWcooked's reversibility check specific to Zen 2 / WRX80. **Workaround:** the resume system retries automatically; use the Xeon workstation for reversal work. A GitHub issue for MediaArea/RAWcooked is planned (Valgrind output + Xeon comparison data).

### RAWcooked reversibility data must be the last attachment
Confirmed by extracting and re-attaching reversibility data in last position, which restored reversal. Reported to MediaArea via GitHub. See [MANUAL.md](MANUAL.md#why-there-is-no-post-encode-attachment-step) for the full explanation of why this ruled out a post-encode `mkvpropedit --add-attachment` step.

## Development approach

- Iterative, incremental — preserve existing step behavior and log format with each change; the resume system depends on exact `>>> SUCCESS: Step N completed successfully.` marker text.
- Verbose terminal output is preferred: every step's subprocess output streams to both stdout and the process log.
- Log files include a consistent header banner identifying the lab; a `SCRIPT RESUMED` header is appended when re-run after interruption.
- Source and output directories should always be separate (`--source-dir` vs `--output-dir`) — mirrors a hard-learned convention from `vdg`, where sharing them caused resume logic to pick up derivative files as sources.
- Preserve all existing logic when adding features — changes should be surgical.

## Planned future work

- **WAV policy tightening** — restrict to 16-bit and 24-bit only, pending SMPL team consultation (currently accepts various bit depths at 48 kHz PCM).
- **RAWcooked GitHub issue** — file with MediaArea documenting the Threadripper segfault (Valgrind output, Xeon comparison).
- **MediaConch policies** — add real `DPX_SMPTE-CORE.xml` / `WAV_policy.xml` to `MediaConch/` from the lab's MediaConch installation.

## Tools & ecosystem

- **RAWcooked** — lossless DPX-to-FFv1/MKV encoding with reversibility; version 25.12 in use at time of writing.
- **MediaConch** — policy-based validation of DPX and WAV files; validates actual file contents, not extensions.
- **mkvtoolnix** (`mkvpropedit`) — MKV metadata tagging; do not use `--add-attachment` post-encode (see Known issues).
- **FFmpeg** — H.264 review derivative generation.
- **[vdg](https://github.com/michaelangeletti/vdg)** — sibling SMPL pipx tool (video derivative generation); `srw` was packaged to match its structure (pyproject.toml + console-script entry point, `bin/` thin wrapper, README/MANUAL/INSTALL split, CLAUDE.md context file).
