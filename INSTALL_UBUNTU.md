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

## 4. Install pipx (if not already present)

```bash
sudo apt install -y pipx
pipx ensurepath
```

Open a new shell (or `source ~/.bashrc`) afterward so pipx's bin directory is on `PATH`.

---

## 5. Install srw

```bash
pipx install git+https://github.com/Stanford-Media-Preservation-Lab/srw.git
```

pipx installs the console script into an isolated environment, matching this lab's other tools (`vdg`, `cta`, `srd`). Note that pushing a commit to the repo does **not** automatically update an already-installed copy — after any update, run:

```bash
pipx reinstall srw
```

> **Installed before the repo moved to the Stanford-Media-Preservation-Lab organization?** pipx remembers the original (`michaelangeletti/srw`) URL. GitHub redirects it, so `pipx reinstall srw` still works, but to point an existing install at the new location run `pipx uninstall srw` and then the `pipx install` command above.

### Development install (contributing to srw itself)

If you're working on `srw`'s own code rather than just running it, install from a local clone in editable mode instead:

```bash
git clone https://github.com/Stanford-Media-Preservation-Lab/srw.git
cd srw
pip3 install -e .
```

---

## 6. Verify installation

```bash
srw --help
srw --version
```

---

## 7. Set up your project directory

`srw` expects a `Source/`, `Documents/`, and `MediaConch/` directory (paths are configurable via flags — see [MANUAL.md](MANUAL.md)). The MediaConch policy XML files (`DPX_SMPTE-CORE.xml`, `WAV_policy.xml`) are lab-specific and are not bundled with this repo; source them from your MediaConch installation or GUI export and place them in `MediaConch/`.

```bash
mkdir -p Source Documents MediaConch
# copy your DPX sequence folders into Source/
# copy DPX_SMPTE-CORE.xml and WAV_policy.xml into MediaConch/
```

---

## Running tests

```bash
pip3 install pytest
pytest
```
