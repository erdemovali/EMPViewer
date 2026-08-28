Build-time icon files (NOT committed - build.py `ensure_icons()` recreates them
on every build):

    app.ico   - Windows application icon
    app.icns  - macOS application icon
    app.png   - PNG copy of the app glyph

Source of truth: the hand-designed set in ../icons/
    icons/app/EMPViewer.ico / .icns / EMPViewer_<size>.png   -> app icon
    icons/filetypes/<ext>.ico / .icns                         -> per-extension
                                                                 Explorer / Finder
                                                                 icons

If ../icons/ is missing, ensure_icons() falls back to down-sampling
../emplogo.png, and failing that a plain brand-blue tile so the build still
succeeds.

THIRD_PARTY_LICENSES.txt IS committed and is bundled into the app (shown under
Help > About > Licenses). Keep it in step with requirements.txt.
