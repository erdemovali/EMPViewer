macOS packaging helpers
=======================

entitlements.plist
    Hardened-runtime entitlements applied to the signed EMPViewer.app.
    Must stay a bare plist with NO XML comments - codesign/AMFI's parser
    (AMFIUnserializeXML) rejects comments ("syntax error near line N").

    Why each key:
      disable-library-validation       load the bundle's own ad-hoc-signed
                                       .so/.dylib files (PyInstaller extracts
                                       dozens); without this the notarized app
                                       is killed at launch -> "app can't be
                                       opened".
      allow-unsigned-executable-memory CPython bytecode / ctypes.
      allow-jit                        CPython / Qt JIT-style allocations.
      allow-dyld-environment-variables the PyInstaller bootstrap sets DYLD_* vars.

    QtWebEngine is NOT used, so no WebEngine/helper entitlements are needed.

sign_and_notarize.sh
    Signs the PyInstaller bundle inside-out (every nested Mach-O first, the
    .app bundle last) with the hardened runtime + entitlements.plist, then
    notarizes and staples. `--dmg-only <dmg>` does the same for a .dmg.
    Used by .github/workflows/build-macos.yml.

set-default.command
    Double-clickable helper that makes EMPViewer the default opener for
    .eml/.msg/.pst/.ost.
