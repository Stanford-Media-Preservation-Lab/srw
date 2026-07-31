# srw

**SMPL RAWcooked Workflow**

`srw` is a batch preservation transcoding tool for digitized motion picture film scans. It takes folders of DPX image sequences (with optional WAV audio) and packages them into lossless FFv1/Matroska (MKV) files for long-term preservation in an OAIS digital repository, with MD5 verification, MediaConch policy validation, embedded metadata tagging, an H.264 review derivative, and a MediaInfo technical metadata dump — all tracked through a resumable, timestamped per-sequence log. Runs on Ubuntu 24.04.

---

## Ten-step workflow

Each sequence folder under `--source-dir` is run through a Step 0 pre-flight check followed by ten numbered steps. Every step writes a `SUCCESS` marker to its process log; if the tool is interrupted or a step fails, re-running `srw` skips everything already completed and resumes from the failure point. `--start-step`/`--end-step` can also run a specific step range on demand — see below.

| Step | Name | Output |
|------|------|--------|
| 0 | Pre-flight Check | Audio Y/N, DPX frame-gap scan, sidecar `.md5` coverage (report only); halts on missing metadata XML, missing MediaConch policy, or insufficient disk space on `--output-dir` |
| 1 | Inventory Generation | `{docs-dir}/{sequence}_inventory.csv` |
| 2 | Checksum Verification | Verifies sidecar `.md5` files, relocates them out of the source folder |
| 3 | MediaConch DPX Validation | Validates every `.dpx` against your DPX policy |
| 4 | MediaConch WAV Validation | Validates any `.wav` against your WAV policy (skipped if picture-only) |
| 5 | Manifest Generation | `{source}/{sequence}/{sequence}.md5` (embedded into the MKV by RAWcooked) |
| 6 | RAWcooked Transcode | `{output-dir}/{sequence}.mkv` + `.log` |
| 7 | Embed Metadata Tags | Tags MKV from `{docs-dir}/{sequence}.xml` via `mkvpropedit` |
| 8 | Generate Review Derivative | `{output-dir}/{sequence}_rawcooked_review.mp4` |
| 9 | MediaInfo Technical Metadata | `{docs-dir}/{sequence}_mediainfo.txt` (`mediainfo -f -i`, for QC/database use) |
| 10 | Final Deliverable Hashes | `.mkv.md5` and `.log.md5` |

See [MANUAL.md](MANUAL.md) for full details on each step, the resume system, `--start-step`/`--end-step`, and key design decisions.

---

## Quick start

```bash
srw run --output-dir /media/smpl-5220r/A/MKV
```

By default `srw run` reads sequences from `./Source`, writes logs/CSVs/XML metadata to `./Documents`, and reads MediaConch policies from `./MediaConch`. Override any of these:

```bash
srw run \
  --source-dir /path/to/Source \
  --output-dir /media/smpl-5220r/A/MKV \
  --docs-dir /path/to/Documents \
  --mediaconch-dir /path/to/MediaConch \
  --attachment-size 5000000
```

See `srw run --help` for the full flag reference.

---

## Batch mode — multiple source/output pairs at once

If DPX sequences are staged across several source locations at once (e.g. multiple batch folders spread across separate RAID volumes, each writing to its own destination drive), `srw batch` launches one `srw run` per batch folder in parallel from a single TOML config, instead of invoking `srw run` by hand for each one:

```bash
srw batch --config batches.toml
```

```toml
# batches.toml
[[batch]]
batch_dir = "/mnt/raid1/batch1"    # expects Source/, Documents/, MediaConch/ inside
output_dir = "/mnt/nvme_a/mkv"

[[batch]]
batch_dir = "/mnt/raid1/batch2"
output_dir = "/mnt/nvme_a/mkv"

[[batch]]
batch_dir = "/mnt/raid2/batch1"
output_dir = "/mnt/nvme_b/mkv"
```

There's no built-in concurrency cap — every configured batch launches at once by default, since the real per-machine ceiling is I/O-bound and worth measuring (`iostat`/`iotop` against the actual RAID/NVMe devices) rather than assuming. Throttle with `--max-parallel N` once you've established one. See [MANUAL.md](MANUAL.md#batch-mode) for the full config reference.

---

## Installation

See [INSTALL_UBUNTU.md](INSTALL_UBUNTU.md).

## Documentation

See [MANUAL.md](MANUAL.md) for the full workflow reference, directory layout, resume system, and known issues.

---

## Dependencies

- Python 3.11+ (stdlib only — no third-party packages required)
- `rawcooked` — lossless DPX-to-FFv1/MKV encoding with reversibility
- `mediaconch` — policy-based validation of DPX and WAV files
- `mkvtoolnix` (`mkvpropedit`) — MKV metadata tagging
- `ffmpeg` — H.264 review derivative generation
- `mediainfo` — technical metadata dump for QC/database use

```bash
sudo apt install rawcooked mediaconch mkvtoolnix ffmpeg mediainfo
```

Only the tools needed by the steps in your `--start-step`/`--end-step` range are checked — e.g. `--start-step 3 --end-step 4` (MediaConch validation only) doesn't require `rawcooked` or `mediainfo` to be installed.

## Required inputs per sequence

- A source folder under `--source-dir` containing the DPX sequence (and optional WAV), each file with a sidecar `.md5` checksum
- `{docs-dir}/{sequence_name}.xml` — custom Matroska metadata tags, required before step 7
- MediaConch policy XML files in `--mediaconch-dir` (see [MANUAL.md](MANUAL.md#mediaconch-policies))

## License

MIT
