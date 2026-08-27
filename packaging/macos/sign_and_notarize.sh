#!/usr/bin/env bash
#
# Sign + notarize + staple EMPViewer for macOS distribution.
#
# PyInstaller / PySide6 bundles must be signed inside-out (every nested Mach-O
# first, the .app bundle last) with the hardened runtime AND an entitlements
# file - otherwise the notarized app is killed at launch by library validation
# and Finder shows "EMPViewer.app can't be opened". `codesign --deep` does not
# do this reliably; this script does it explicitly.
#
# Usage:
#   packaging/macos/sign_and_notarize.sh <path-to-.app>       # sign+notarize+staple the app bundle
#   packaging/macos/sign_and_notarize.sh --dmg-only <.dmg>    # sign+notarize+staple a .dmg
#
# Required environment:
#   SIGN_IDENTITY   e.g. "Developer ID Application: Ad Soyad (TEAMID)"
#   AC_APPLE_ID     Apple ID e-mail
#   AC_PASSWORD     app-specific password (appleid.apple.com)
#   AC_TEAM_ID      10-char Team ID
# Optional:
#   ENTITLEMENTS    hardened-runtime entitlements plist
#                   (default: entitlements.plist next to this script)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENTITLEMENTS="${ENTITLEMENTS:-$SCRIPT_DIR/entitlements.plist}"

DMG_ONLY=0
if [ "${1:-}" = "--dmg-only" ]; then
  DMG_ONLY=1
  shift
fi

TARGET="${1:-}"
if [ -z "$TARGET" ] || [ ! -e "$TARGET" ]; then
  echo "error: target not found: '${TARGET:-<missing>}'" >&2
  echo "usage: $0 <app-bundle> | --dmg-only <dmg>" >&2
  exit 2
fi

: "${SIGN_IDENTITY:?SIGN_IDENTITY not set}"
: "${AC_APPLE_ID:?AC_APPLE_ID not set}"
: "${AC_PASSWORD:?AC_PASSWORD not set}"
: "${AC_TEAM_ID:?AC_TEAM_ID not set}"

if [ "$DMG_ONLY" -eq 0 ] && [ ! -f "$ENTITLEMENTS" ]; then
  echo "error: entitlements file not found: $ENTITLEMENTS" >&2
  exit 2
fi

echo "==> Signing identity check"
if ! security find-identity -v -p codesigning | grep -qF "$SIGN_IDENTITY"; then
  echo "error: signing identity not in keychain: $SIGN_IDENTITY" >&2
  security find-identity -v -p codesigning >&2 || true
  exit 3
fi

# --------------------------------------------------------------------------- #
# notarize <path>  --  submit, block on the result, fail loudly if not Accepted
# --------------------------------------------------------------------------- #
notarize() {
  local path="$1" upload out id status
  if [[ "$path" == *.app ]]; then
    upload="$path.notarize.zip"
    rm -f "$upload"
    ditto -c -k --keepParent "$path" "$upload"
  else
    upload="$path"                       # a .dmg is submitted as-is
  fi

  echo "==> notarytool submit ($path)"
  out="$(xcrun notarytool submit "$upload" \
          --apple-id "$AC_APPLE_ID" --password "$AC_PASSWORD" --team-id "$AC_TEAM_ID" \
          --wait --output-format json)"
  echo "$out"

  id="$(printf '%s' "$out"     | python3 -c 'import sys,json; print(json.load(sys.stdin).get("id",""))')"
  status="$(printf '%s' "$out" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("status",""))')"

  if [ "$status" != "Accepted" ]; then
    echo "error: notarization status='$status' (id=$id)" >&2
    if [ -n "$id" ]; then
      xcrun notarytool log "$id" \
        --apple-id "$AC_APPLE_ID" --password "$AC_PASSWORD" --team-id "$AC_TEAM_ID" >&2 || true
    fi
    exit 4
  fi

  [[ "$path" == *.app ]] && rm -f "$upload"
  return 0
}

# --------------------------------------------------------------------------- #
# .dmg path: sign the container, notarize, staple
# --------------------------------------------------------------------------- #
if [ "$DMG_ONLY" -eq 1 ]; then
  echo "==> codesign (dmg) $TARGET"
  codesign --force --timestamp --sign "$SIGN_IDENTITY" "$TARGET"
  codesign --verify --verbose=2 "$TARGET"
  notarize "$TARGET"
  xcrun stapler staple "$TARGET"
  xcrun stapler validate "$TARGET"
  spctl -a -vvv --type install "$TARGET" || true   # 'install' assessment is informational for a dmg
  echo "==> DMG signed + notarized + stapled: $TARGET"
  exit 0
fi

# --------------------------------------------------------------------------- #
# .app path: sign inside-out, verify, notarize, staple
# --------------------------------------------------------------------------- #
APP="$TARGET"
COMMON_ARGS=(--force --timestamp --options runtime --entitlements "$ENTITLEMENTS" --sign "$SIGN_IDENTITY")

echo "==> Signing nested Mach-O binaries (dylib / so / executables)"
while IFS= read -r -d '' f; do
  case "$f" in
    *.dylib|*.so) ;;                                   # always Mach-O
    *) file -b "$f" | grep -q 'Mach-O' || continue ;;  # only real Mach-O executables
  esac
  codesign "${COMMON_ARGS[@]}" "$f"
done < <(find "$APP/Contents" -type f \( -name '*.dylib' -o -name '*.so' -o -perm -111 \) -print0)

echo "==> Signing nested framework bundles"
while IFS= read -r -d '' fw; do
  codesign --force --timestamp --options runtime --sign "$SIGN_IDENTITY" "$fw"
done < <(find "$APP/Contents" -type d -name '*.framework' -print0)

echo "==> Signing main executable"
codesign "${COMMON_ARGS[@]}" "$APP/Contents/MacOS/EMPViewer"

echo "==> Sealing the app bundle"
codesign "${COMMON_ARGS[@]}" "$APP"

echo "==> Verifying signature"
codesign --verify --deep --strict --verbose=2 "$APP"
codesign -d --entitlements :- "$APP" || true

notarize "$APP"

echo "==> Stapling"
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"
spctl -a -vvv --type execute "$APP"

echo "==> App signed + notarized + stapled: $APP"
