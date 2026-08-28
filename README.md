# EMPViewer

A portable desktop viewer for **`.eml`**, **`.msg`**, **`.pst`** and **`.ost`** mail
files, for **Windows and macOS**. Built with Python 3.11+ and PySide6. No Chromium,
instant start. (Runs from source anywhere Python + Qt do.)

**Website:** <https://erdemovali.github.io/EMPViewer/> &nbsp;·&nbsp; **Downloads:** [Releases](https://github.com/erdemovali/EMPViewer/releases/latest)

![layout](assets/app.png)

## Features

- **Formats**
  - `.eml` — standard-library `email` module (always available).
  - `.msg` — [`extract_msg`](https://pypi.org/project/extract-msg/): headers, HTML/text
    body, inline images, attachments, RTF fallback.
  - `.pst` / `.ost` — pluggable backend, auto-detected at runtime:
    1. **built-in** pure-Python [MS-PST] reader (`parsers/pst_native.py`) — **no
       dependencies**, always available, the default. Unicode PST/OST; none /
       permute / cyclic encryption.
    2. **`libpff`** via `pypff` if importable.
    3. **`readpst`** (the `libpst` CLI) if on `PATH`.
    `.ost` uses the same code path; modern profile-encrypted `.ost` files are
    reported cleanly as unreadable rather than crashing.

    Diagnose a specific file:
    `python -m parsers.pst_native "path\to\file.pst" --dump-first`
- **UI** — resizable panes: a Library tree on the left (hover a row for an **✕** to
  close it; right-click also works), a rich message viewer with an attachment bar on
  the right, and a Sender/Subject/Date message list that appears **only** when a
  PST/OST folder is selected — a single `.eml`/`.msg` gets the whole viewer pane.
  App icon is rendered from `emplogo.png`. System / Light / Dark themes, remembered
  across restarts.
- **Attachments** — click to open with the OS default app (a copy is written to a
  per-session temp dir that is wiped on exit); right-click for **Save As…**.
- **Privacy** — remote images / tracking pixels are blocked until you click
  *Load remote content*; inline `cid:` images always render offline.
- **Drag & drop** — drop any supported file onto the window to open it.
- **Double-click / file association**
  - Windows: path is read from `sys.argv`.
  - macOS: `QFileOpenEvent` is handled, including events fired before the window exists.
- **Responsive** — every parse and PST folder/message load runs on a `QThreadPool`
  worker; the UI never blocks, even on large `.pst` files.

## Quick start

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS:    source .venv/bin/activate
pip install -r requirements.txt

python main.py                       # empty window
python main.py "C:\path\to\mail.eml" # open a file (same path a double-click uses)
```

> **No extra setup for any format.** `.pst`/`.ost` are read by a built-in
> pure-Python parser. `libpff-python` or a `readpst` binary, if present, are used
> in preference but are entirely optional.

## File associations & "default app"

> Full distribution + default-handler guide (portable vs installer, Windows &
> macOS, enterprise/GPO): **[`docs/DAGITIM.md`](docs/DAGITIM.md)** (Turkish).

### Windows

Per-user, no admin rights:

```bat
EMPViewer.exe --register       :: register as a handler for .eml/.msg/.pst/.ost
EMPViewer.exe --set-default    :: register + open Settings ▸ Default apps
EMPViewer.exe --unregister
```

`--register` writes `HKCU\Software\Classes` ProgIDs + a `Capabilities` block under
`HKCU\Software\EMPViewer` + `RegisteredApplications`, so EMPViewer shows up in
**Open with** and in **Settings ▸ Default apps**. Windows 10/11 will **not** let any
app silently take over a file type another app already owns (Outlook owns `.msg`
and `.pst`) — the user confirms the switch once in the Default-apps UI, which
`--set-default` opens for them. For an unclaimed type it usually becomes default
immediately.

### Windows installer (`EMPViewer-Setup.exe`)

```bash
python build.py --installer     # runs PyInstaller --onedir, then Inno Setup
```

Needs [Inno Setup 6](https://jrsoftware.org/isdl.php) (`iscc` on PATH). The script
is `packaging/EMPViewer.iss`; it installs per-user (no admin prompt), runs
`--register` on install and `--unregister` on uninstall, and offers an optional
"open Default apps" checkbox. An MSI/MSIX is not provided — for an unattended
enterprise rollout, an MSIX manifest can declare the same associations and Windows
honours them without the confirmation prompt.

### macOS

The packaged `.app` already declares the document types (`CFBundleDocumentTypes`
via `build.py`). Set EMPViewer as the handler with Finder ▸ *Get Info* ▸
*Open with* ▸ *Change All…*, or `duti` for scripted setup.

## Building a portable binary

```bash
python build.py            # Windows -> dist/EMPViewer.exe (onefile)
                           # macOS   -> dist/EMPViewer.app
python build.py --onedir   # faster cold start (folder instead of single file)
python build.py --dmg      # macOS: also produce dist/EMPViewer.dmg
python build.py --clean    # wipe build/ and dist/ first
```

`--onefile` self-extracts to a temp dir on first launch (~1–2 s). Use `--onedir` when
true instant start matters more than shipping a single file.

## Build & release

Release binaries are built **only from this repository's source** by GitHub Actions —
the build scripts and CI configuration are in the repo:

| Workflow | Runner | Produces |
|---|---|---|
| `.github/workflows/build-windows.yml` | `windows-latest` | `EMPViewer.exe`, `EMPViewer-Setup.exe` (Authenticode-signed via SignPath when configured) |
| `.github/workflows/build-macos.yml` | `macos-14` / `macos-13` | `EMPViewer-macos-{arm64,x86_64}.dmg` (Developer ID signed + notarized) |

The version is the single line in **`VERSION`**. To cut a release: bump `VERSION`,
commit, then `git tag vX.Y.Z && git push --tags`. Both workflows run on the tag, the
Windows installer is submitted to SignPath for signing (which **requires a manual
approval** in the SignPath dashboard), and the `release` job attaches every artifact
to the GitHub Release.

## Project layout

```
main.py                 Entry point; QApplication subclass; argv + macOS FileOpen.
build.py                PyInstaller automation (.exe / .app / .dmg).
ui/main_window.py       Window, splitters, sidebar tree, message table, DnD, menus.
ui/viewer_widget.py     Header + QTextBrowser body (remote-blocking) + attachment bar.
ui/theme.py             System/Light/Dark palettes + QSettings persistence.
parsers/models.py       Format-agnostic dataclasses shared by parsers and UI.
parsers/loader.py       Extension -> parser dispatch.
parsers/eml_parser.py   .eml extraction.
parsers/msg_parser.py   .msg extraction.
parsers/pst_parser.py   PstBackend ABC + native / libpff / readpst backends.
parsers/pst_native.py   Built-in pure-Python [MS-PST] reader (.pst / .ost).
parsers/_rtf.py         Pure-Python compressed-RTF (LZFu) decompressor.
parsers/errors.py       Typed, user-facing exceptions.
utils/helpers.py        Resource paths (_MEIPASS), temp files, OS hand-off.
utils/workers.py        QRunnable workers + WorkerSignals.
utils/win_integration.py  Windows --register / --unregister.
tests/                  pytest suite (.eml fully covered; .msg/.pst need fixtures).
```

## Tests

```bash
pip install pytest
pytest
```

`.eml` and the helper/dispatch layers are covered without any third-party library.
`.msg` and `.pst` round-trip tests activate automatically when you place
`tests/data/sample.msg` / `tests/data/sample.pst`.

## Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io), certificate by
[SignPath Foundation](https://signpath.org). This covers the Windows builds; macOS builds
are signed with an Apple Developer ID and notarized.

- **Committers:** Erdem Ovali ([@erdemovali](https://github.com/erdemovali))
- **Reviewers:** Erdem Ovali ([@erdemovali](https://github.com/erdemovali)) — every change that
  is not authored by a committer (e.g. a pull request) is reviewed before merge.
- **Approvers:** Erdem Ovali ([@erdemovali](https://github.com/erdemovali)) — each signing
  request is approved manually in the SignPath dashboard before a release is signed.

Binaries are built only from the source in this repository, by the GitHub Actions
workflows described under **Build & release** above.

**Privacy:** this program will not transfer any information to other networked systems
unless specifically requested by the user or the person installing or operating it.
Remote content in messages (images, tracking pixels) is blocked until you explicitly
click *Load remote content*.

See also [`SECURITY.md`](SECURITY.md).

## License

MIT (see `LICENSE`).
