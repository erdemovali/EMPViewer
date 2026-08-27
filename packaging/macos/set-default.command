#!/bin/bash
# Make EMPViewer.app the default opener for .eml / .msg / .pst / .ost.
# Double-click this file in Finder, or run it from Terminal.
#
# Prefers `duti` (brew install duti); falls back to LaunchServices + lsregister.

set -u
BUNDLE_ID="com.empviewer.app"
EXTS=(eml msg pst ost)

echo "Setting ${BUNDLE_ID} as the default handler for: ${EXTS[*]}"

if command -v duti >/dev/null 2>&1; then
    for ext in "${EXTS[@]}"; do
        duti -s "$BUNDLE_ID" ".$ext" all && echo "  .$ext  -> OK"
    done
else
    echo "(duti not found - using LaunchServices directly)"
    SECURE="com.apple.LaunchServices/com.apple.launchservices.secure"
    for ext in "${EXTS[@]}"; do
        defaults write "$SECURE" LSHandlers -array-add \
          "{LSHandlerContentTag=$ext;LSHandlerContentTagClass=public.filename-extension;LSHandlerRoleAll=$BUNDLE_ID;}"
        echo "  .$ext  -> queued"
    done
    LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
    "$LSREGISTER" -kill -r -domain local -domain system -domain user
    echo "LaunchServices database rebuilt."
fi

echo
echo "Done. If Finder still opens a type elsewhere, select a file, press Cmd-I,"
echo "expand 'Open with', choose EMPViewer and click 'Change All...'."
