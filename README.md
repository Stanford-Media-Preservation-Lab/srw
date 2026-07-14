# srw

**SMPL RAWcooked Workflow** — Stanford Media Preservation Lab

`srw` is a batch preservation transcoding tool for digitized motion picture film scans. It takes folders of DPX image sequences (with optional WAV audio) and packages them into lossless FFv1/Matroska (MKV) files for long-term preservation in an OAIS digital repository, with MD5 verification, MediaConch policy validation, embedded metadata tagging, and an H.264 review derivative — all tracked through a resumable, timestamped per-sequence log. Runs on Ubuntu 24.04.

---

## Nine-step workflow

Each sequence folder under `--source-dir` is run through nine steps. Every step writes a `SUCCESS` marker to its process log; if the tool is interrupted or a step fails, re-running `srw` skips everything already completed and resumes from the failure point.

| Step | Name | Output |
|------|------|--------|
| 1 | Inventory Generation | `{docs-dir}/{sequence}_inventory.csv` |
| 2 | Checksum Verification | Verifies + deletes sidecar `.md5` files |
| 3 | MediaConch DPX Validation | Validates every `.dpx` against your DPX policy |
| 4 | MediaConch WAV Validation | Validates any `.wav` against your WAV policy (skipped if picture-only) |
| 5 | Manifest Generation | `{source}/{sequence}/{sequence}.md5` (embedded into the MKV by RAWcooked) |
| 6 | RAWcooked Transcode | `{output-dir}/{sequence}.mkv` + `.log` |
| 7 | Embed Metadata Tags | Tags MKV from `{docs-dir}/{sequence}.xml` via `mkvpropedit` |
| 8 | Generate Review Derivative | `{output-dir}/{sequence}_rawcooked_review.mp4` |
| 9 | Final Deliverable Hashes | `.mkv.md5` and `.log.md5` |

See [MANUAL.md](MANUAL.md) for full details on each step, the resume system, and key design decisions.

---

## Quick start

```bash
srw --output-dir /media/smpl-5220r/A/MKV
```

By default `srw` reads sequences from `./Source`, writes logs/CSVs/XML metadata to `./Documents`, and reads MediaConch policies from `./MediaConch`. Override any of these:

```bash
srw \
  --source-dir /path/to/Source \
  --output-dir /media/smpl-5220r/A/MKV \
  --docs-dir /path/to/Documents \
  --mediaconch-dir /path/to/MediaConch \
  --attachment-size 5000000
```

See `srw --help` for the full flag reference.

---

## Installation

See [INSTALL_UBUNTU.md](INSTALL_UBUNTU.md).

## Documentation

See [MANUAL.md](MANUAL.md) for the full workflow reference, directory layout, resume system, and known issues.

---

## Dependencies

- Python 3.10+ (stdlib only — no third-party packages required)
- `rawcooked` — lossless DPX-to-FFv1/MKV encoding with reversibility
- `mediaconch` — policy-based validation of DPX and WAV files
- `mkvtoolnix` (`mkvpropedit`) — MKV metadata tagging
- `ffmpeg` — H.264 review derivative generation

```bash
sudo apt install rawcooked mediaconch mkvtoolnix ffmpeg
```

## Required inputs per sequence

- A source folder under `--source-dir` containing the DPX sequence (and optional WAV), each file with a sidecar `.md5` checksum
- `{docs-dir}/{sequence_name}.xml` — custom Matroska metadata tags, required before step 7
- MediaConch policy XML files in `--mediaconch-dir` (see [MANUAL.md](MANUAL.md#mediaconch-policies))

## License

MIT
