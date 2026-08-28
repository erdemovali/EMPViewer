# Security Policy

## Reporting a vulnerability

Please report suspected security issues privately via GitHub's
**[Report a vulnerability](https://github.com/erdemovali/EMPViewer/security/advisories/new)**
form (Security ▸ Advisories), or by opening a minimal issue that does not include
exploit details and asking for a private channel.

Please do not disclose publicly until a fix is available. Expect an initial
response within about a week.

## Supported versions

The latest release on the [Releases](https://github.com/erdemovali/EMPViewer/releases)
page is the only supported version.

## Scope notes

- EMPViewer opens untrusted mail files (`.eml`, `.msg`, `.pst`, `.ost`). Parsing
  hostile input is in scope (crashes, path traversal on attachment save, etc.).
- Message bodies render in Qt's `QTextBrowser` (no Chromium, no JavaScript).
  Remote content (images, tracking pixels) is blocked until the user explicitly
  loads it.
- The application does not transfer any information to other networked systems
  unless specifically requested by the user. The only outbound request it can
  make is the **opt-in** update check (Help ▸ Check for Updates, or the
  Preferences toggle), an unauthenticated `GET` to `api.github.com`. That
  request necessarily exposes the client IP address and a static
  `User-Agent: EMPViewer-update-check` to GitHub; it carries no other data. The
  update check never downloads or runs code - it only opens the Releases page in
  the system browser.
- Attachments opened with "Open" are written to a per-process temp directory
  (`empviewer-*`) with `0600` permissions and removed at exit. A hard crash or
  kill can leave that directory behind; it contains only copies of attachments
  you opened.
- The macOS build ships with the `com.apple.security.cs.allow-unsigned-executable-memory`
  entitlement. It is required for the bundled CPython interpreter (bytecode +
  `ctypes`) to run under the hardened runtime; it is not used to load external
  code.
- Dependencies in `requirements.txt` are currently pinned with `>=` lower
  bounds. Reproducible, hash-pinned builds are tracked as a follow-up.

## Code signing

Windows builds are Authenticode-signed through the
[SignPath Foundation](https://signpath.org) open-source program; macOS builds are
signed with an Apple Developer ID and notarized. See the **Code signing policy**
section in the [README](README.md).
