# Crypto Scanner

A real-time cryptocurrency scalping scanner for Binance markets with multi-channel alerts.

## Features

- Live scanning of Binance spot and futures markets
- Signal quality filtering for high-probability setups
- Multi-channel alerts: sound, desktop notifications, Telegram, WhatsApp
- Auto-scanning with sort-state preservation
- PyQt6 GUI

---

## Download

Go to the [Releases](https://github.com/ZAKhan/crypto-scanner/releases) page and download the binary for your platform:

| Platform | File |
|----------|------|
| Windows  | `crypto_scanner.exe` |
| macOS    | `crypto_scanner_macos` |
| Linux    | `crypto_scanner_ubuntu` |

No Python installation required — binaries are self-contained.

### macOS note
On first run macOS will block the app since it is unsigned. To bypass:
- Right-click the file → **Open** → **Open** again in the dialog

### Linux note
Make the binary executable before running:
```bash
chmod +x crypto_scanner_ubuntu
./crypto_scanner_ubuntu
```

---

## Running from Source

### Requirements

- Python 3.11+
- pip

### Install dependencies

```bash
pip install -r requirements.txt
```

### Run

```bash
python crypto_scanner.py
```

---

## Building Binaries

Binaries are built automatically via GitHub Actions on every push. Three platforms are built in parallel: Windows, macOS, and Linux.

To trigger a build manually:
1. Go to the **Actions** tab on GitHub
2. Select **Build Binaries**
3. Click **Run workflow**

Build artifacts are available for download from the Actions run page (requires GitHub login).
Published binaries are attached to each [Release](https://github.com/ZAKhan/crypto-scanner/releases).

---

## Releasing a New Version

Use the included release script:

```bash
./push_release.sh
```

It will:
1. Show changed files
2. Ask for a version number and commit message
3. Commit, push, and tag the release
4. Print a direct link to create the GitHub Release

Once GitHub Actions finishes building (~5 minutes), go to the printed link, download the 3 artifacts, attach the binaries, and publish.

---

## Project Structure

```
cryptoscanner/
├── crypto_scanner.py        # Main application
├── requirements.txt         # Python dependencies
├── push_release.sh          # Release helper script
└── .github/
    └── workflows/
        └── build.yml        # GitHub Actions build workflow
```

---

## Dependencies

- [PyQt6](https://pypi.org/project/PyQt6/) — GUI framework
- [python-binance](https://pypi.org/project/python-binance/) — Binance API client
- [requests](https://pypi.org/project/requests/) — HTTP client
- [PyInstaller](https://pyinstaller.org/) — Binary packaging

---

## License

Private project. Not for redistribution.
