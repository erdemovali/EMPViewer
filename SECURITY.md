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
  unless specifically requested by the user.

## Code signing

Windows builds are Authenticode-signed through the
[SignPath Foundation](https://signpath.org) open-source program; macOS builds are
signed with an Apple Developer ID and notarized. See the **Code signing policy**
section in the [README](README.md).
