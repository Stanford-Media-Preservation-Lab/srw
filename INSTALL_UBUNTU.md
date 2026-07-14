# Installation — Ubuntu 24.04 LTS

`srw` targets Ubuntu 24.04. Its dependencies (`rawcooked`, `mediaconch`, `mkvtoolnix`) are Linux tools without a supported macOS path, so this is currently the only supported platform.

---

## 1. Update package index

```bash
sudo apt update && sudo apt upgrade -y
```

---

## 2. Install RAWcooked, MediaConch, mkvtoolnix, and FFmpeg

Ubuntu's default repos carry older versions. Install the current releases from the MediaArea repository:

```bash
wget https://mediaarea.net/repo/deb/repo-mediaarea_1.0-27_all.deb
sudo dpkg -i repo-mediaarea_1.0-27_all.deb
sudo apt update
sudo apt install -y rawcooked mediaconch mkvtoolnix ffmpeg
```

> If the `.deb` filename above 404s, MediaArea has published a newer repo package — check [mediaarea.net/en/Repos](https://mediaarea.net/en/Repos) for the current version number and substitute it above.

Verify:

```bash
rawcooked --version
mediaconch --version
mkvpropedit --version
ffmpeg -version
```

---

## 3. Install Python 3.10+

Ubuntu 24.04 ships with Python 3.12. Verify:

```bash
python3 --version
```

Install pip if not present:

```bash
sudo apt install -y python3-pip
```

---

## 4. Clone the repository

```bash
git clone https://github.com/michaelangeletti/srw.git
cd srw
```

---

## 5. Install the package

```bash
pip3 install -e .
```

If your user `bin` directory is not in `PATH`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Add to `~/.bashrc` to make permanent.

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
