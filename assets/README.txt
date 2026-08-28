Generated at build time by build.py from ../emplogo.png (the single logo source):

    app.ico   - Windows icon  (256px)
    app.icns  - macOS icon set (16..512, via iconutil)
    app.png   - 256px PNG

These are NOT committed - `build.py ensure_icons()` re-creates them on every
build. If emplogo.png can't be read, build.py falls back to a plain brand-blue
tile so the build still succeeds.
