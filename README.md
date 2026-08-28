# EMPViewer

A desktop viewer for `.eml`, `.msg`, `.pst`, `.ost`, `.mbox`, `.ics` and `.vcf`
files. Windows and macOS. Built with Python and PySide6.

<p>
  <img src="icons/filetypes/eml_128.png" alt=".eml" width="46" height="46">
  <img src="icons/filetypes/msg_128.png" alt=".msg" width="46" height="46">
  <img src="icons/filetypes/pst_128.png" alt=".pst" width="46" height="46">
  <img src="icons/filetypes/ost_128.png" alt=".ost" width="46" height="46">
</p>

Downloads: [Releases](https://github.com/erdemovali/EMPViewer/releases)

## Features

- Opens `.eml`, `.msg`, `.pst`, `.ost` and `.mbox` mail, plus `.ics` calendar
  invites and `.vcf` contact cards.
- `.eml` uses the Python standard library. `.msg` uses
  [`extract_msg`](https://pypi.org/project/extract-msg/).
- `.pst` and `.ost` are read by a built-in pure-Python parser with no external
  dependencies. `libpff` (via `pypff`) or the `readpst` command-line tool are
  used automatically when they are available.
- **Full-text search** across every open mailbox — free terms plus `from:`,
  `to:`, `subject:`, `has:attach`, `after:` / `before:` filters.
- Message list with attachment / size / unread columns and an optional
  group-by-conversation view; tabbed viewing with back / forward history.
- Message view with all headers, a verbatim source view, HTML or plain-text
  body, inline images, and adjustable text size.
- Remote images and tracking pixels are blocked until you choose to load them,
  per message or per sender.
- `winmail.dat` / TNEF attachments are unpacked; attached e-mails open in place.
- Export a folder or a whole PST to a tree of `.eml` files.
- Attachments open with the system default application, or can be saved
  individually or all at once.
- Library sidebar, drag and drop to open, system / light / dark themes,
  English and Turkish.
- Parsing runs off the UI thread, so large files do not freeze the window; a
  size check warns before opening a very large store.

## Run from source

Requires Python 3.11 or newer.

```
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python main.py                   # open an empty window
python main.py path/to/mail.eml  # open a file
```

No extra setup is needed for any format. `libpff-python` and `readpst` are
optional.

## File associations

### Windows

```
EMPViewer.exe --register     register as a handler for .eml/.msg/.pst/.ost
EMPViewer.exe --set-default  register, then open Settings > Default apps
EMPViewer.exe --unregister   remove the registration
```

`--register` writes per-user entries under `HKCU\Software\Classes` and
`RegisteredApplications`, so EMPViewer appears in "Open with" and in
Settings > Default apps. Windows will not silently reassign a type that another
application already owns (Outlook owns `.msg` and `.pst`); confirm the change
once in Settings > Default apps. The installer runs `--register` on install and
`--unregister` on uninstall.

### macOS

The packaged app declares the document types. Set EMPViewer as the handler in
Finder with Get Info > Open with > Change All, or run
`packaging/macos/set-default.command`.

## Building

```
python build.py             # Windows: EMPViewer.exe   macOS: EMPViewer.app
python build.py --onedir     # a folder instead of a single file
python build.py --dmg        # macOS: also build a .dmg
python build.py --installer  # Windows: EMPViewer-Setup.exe (requires Inno Setup 6)
python build.py --clean      # wipe build/ and dist/ first
```

The hand-designed icon set lives in `icons/` — `icons/app/` is the application
icon (Windows `.ico`, macOS `.icns`, glyph PNGs for the runtime window/dock
icon) and `icons/filetypes/` holds a distinct icon per extension
(`.eml` / `.msg` / `.pst` / `.ost`). `build.py` copies these into the packaged
app; the Windows `--register` step and the macOS `Info.plist` document types
point Explorer / Finder at the per-extension icons. If `icons/` is absent the
build falls back to down-sampling `emplogo.png`.

## Releases

Release binaries are built only from this repository by GitHub Actions:

- `.github/workflows/build-windows.yml` produces `EMPViewer.exe` and
  `EMPViewer-Setup.exe`.
- `.github/workflows/build-macos.yml` produces `EMPViewer-macos-arm64.dmg` and
  `EMPViewer-macos-x86_64.dmg`.

The version is the single line in `VERSION`. To publish a release, update
`VERSION`, commit, then:

```
git tag vX.Y.Z
git push origin vX.Y.Z
```

Both workflows run on the tag and attach their binaries to the GitHub Release.

## Project layout

```
main.py                  Entry point; argv and macOS file-open handling.
build.py                 PyInstaller build automation.
ui/main_window.py        Window, sidebar tree, message list, drag and drop, menus.
ui/viewer_widget.py      Header, message body, attachment bar.
ui/theme.py              System / light / dark palettes and persistence.
parsers/loader.py        Extension to parser dispatch.
parsers/models.py        Shared data classes.
parsers/eml_parser.py    .eml extraction.
parsers/msg_parser.py    .msg extraction.
parsers/pst_parser.py    PST/OST backends (native / libpff / readpst).
parsers/pst_native.py    Built-in pure-Python PST/OST reader.
utils/helpers.py         Resource paths, temp files, OS hand-off.
utils/workers.py         Background workers.
tests/                   pytest suite.
```

## Tests

```
pip install pytest
pytest
```

`.msg` and `.pst` round-trip tests run when sample files are placed in
`tests/data/`.

## Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io),
certificate by [SignPath Foundation](https://signpath.org). This covers the
Windows builds. macOS builds are signed with an Apple Developer ID and notarized.

- Committers, reviewers and approvers: [@erdemovali](https://github.com/erdemovali)
- Every change that does not come from a committer is reviewed before merge.
- Every signing request is approved manually before a release is signed.

Binaries are built only from the source in this repository, by the GitHub
Actions workflows listed above.

This program does not transfer any information to other networked systems unless
requested by the user or the person installing or operating it. Remote content
in messages is blocked until it is loaded explicitly. The only outbound request
is an optional update check (Help > Check for Updates, or a Preferences opt-in)
that fetches the latest release tag from the GitHub API. See
[SECURITY.md](SECURITY.md) for reporting vulnerabilities.

## License

EMPViewer's own source code is under the MIT License (see [LICENSE](LICENSE)).

Packaged builds bundle third-party components, including **extract-msg (GPLv3)**
and **Qt / PySide6 (LGPLv3)**. Because a GPLv3 component is linked in, the
compiled binaries are distributed under the GPLv3; the complete corresponding
source is this repository. Full details and links are in
[`assets/THIRD_PARTY_LICENSES.txt`](assets/THIRD_PARTY_LICENSES.txt) and in the
app under Help > About > Licenses.
