# Installation — Ubuntu 24.04 LTS

`srw` v1.5.0 · October 2026

`srw` targets Ubuntu 24.04. Its dependencies (`rawcooked`, `mediaconch`, `mkvtoolnix`, `mediainfo`) are Linux tools without a supported macOS path, so this is currently the only supported platform.

---

## 1. Update package index

```bash
sudo apt update && sudo apt upgrade -y
```

---

## 2. Install RAWcooked, MediaConch, mkvtoolnix, FFmpeg, and MediaInfo

Ubuntu's default repos carry older versions. Install the current releases from the MediaArea repository:

```bash
wget https://mediaarea.net/repo/deb/repo-mediaarea_1.0-27_all.deb
sudo dpkg -i repo-mediaarea_1.0-27_all.deb
sudo apt update
sudo apt install -y rawcooked mediaconch mkvtoolnix ffmpeg mediainfo
```

> If the `.deb` filename above 404s, MediaArea has published a newer repo package — check [mediaarea.net/en/Repos](https://mediaarea.net/en/Repos) for the current version number and substitute it above.

Verify:

```bash
rawcooked --version
mediaconch --version
mkvpropedit --version
ffmpeg -version
mediainfo --version
```

---

## 3. Install Python 3.11+

Ubuntu 24.04 ships with Python 3.12. Verify:

```bash
python3 --version
```

---

## 4. Get srw (no installation)

`srw` is **not** installed as a package. It runs straight from a clone of the repo, so every workstation's copy stays visible and independent of any Python environment. Clone it once per workstation, somewhere outside your batch folders:

```bash
cd ~
git clone https://github.com/Stanford-Media-Preservation-Lab/srw.git
```

To update a workstation later:

```bash
git -C ~/srw pull
```

> **Migrating from a pipx install?** Run `pipx uninstall srw` so an old installed copy can never be picked up by mistake.

---

## 5. Verify

```bash
~/srw/bin/srw --version
~/srw/bin/srw --help
```

---

## 6. Set up a batch folder and run

`srw run` works on the directory you run it from: by default it reads `./Source`, writes logs, inventories and metadata to `./Documents`, and reads policies from `./MediaConch`. Give each batch its own folder with that structure (layout in [MANUAL.md](MANUAL.md#directory-structure)). The repo's `MediaConch/` folder contains the lab's `DPX_SMPTE-CORE.xml` and `WAV_policy.xml` — copy them into each batch folder's `MediaConch/`.

Each batch folder also gets its own small launcher script, copied from the repo's template. It's where the (rarely used) `SKIP_STEPS` setting lives, so a skip can only ever affect the one folder whose launcher you edited:

```bash
cp ~/srw/templates/run_srw.py "/media/smpl-5220r/RAID 1/RAWcooked_1/batch_1/"
cd "/media/smpl-5220r/RAID 1/RAWcooked_1/batch_1"
./run_srw.py run --output-dir /media/smpl-5220r/A/MKV
```

If `srw` isn't cloned at `~/srw`, edit `SRW_REPO` at the top of `run_srw.py`. The launcher takes the same arguments as `srw` itself. `--output-dir` is required (it is where the `.mkv`, review `.mp4`, `.log` and hashes go).

**Several jobs at once:** open one terminal per batch folder, `cd` into each, and run `./run_srw.py run --output-dir …` in each. Each batch folder has its own `Documents/`, so process logs and resume state never overlap. Alternatively, `srw batch` can launch several folders from one TOML config (see [MANUAL.md](MANUAL.md#batch-mode)).

**Updating launchers:** `git pull` updates `srw` itself; the launchers in your batch folders don't need to change.

---

## Running tests

```bash
pip3 install pytest
pytest
```
