# Packaging the planner as a standalone program

Goal: Anafi owners download one file for their system, double-click it, and
the planner opens. No Python, no `uv`, no command line.

## Trial build (2026-10-31, Linux x86_64)

PyInstaller bundles Python, the libraries (GDAL included, through rasterio)
and our code into one folder with an executable:

```bash
uv run --with pyinstaller pyinstaller --noconfirm \
  --name anafi-planner --paths . \
  --add-data "$PWD/ui/static:ui/static" \
  --collect-data pyproj --collect-all rasterio --collect-all rio_tiler \
  --collect-all morecantile --hidden-import send2trash \
  entry.py          # entry.py: from ui.__main__ import main; sys.exit(main())
```

(On Windows the `--add-data` separator is `;` instead of `:`.)

Result:

- Build time about 30 s; folder 277 MB, 97 MB as a `.tar.gz`.
- Run from an unrelated folder with an empty environment (`env -i`), so it
  couldn't use the project's Python: the page, planning, Esri tiles, imagery
  date, a WMS layer (Costa Rica's SNIT), and loading a real 93 MB orthophoto
  with its map tiles all worked.
- The first build left out `morecantile`'s data files and orthophoto tiles
  failed (`InvalidIdentifier: WebMercatorQuad`); `--collect-all morecantile`
  fixed it. Expect a few such fixes per platform: test every feature in the
  built program, not only in the development setup.
- Not included: Olympe (pack check, laptop flying) and `afcclient` (iPhone
  transfer), see the plan below.

## Plan

Tracked in [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md), section 4, in this order:

1. **User data folder.** Today missions, orthophotos, WMS layers and battery
   checks are written relative to the folder the program starts in. An installed
   app must use the per-user data folder (`~/.local/share/anafi-mission-planner`,
   `~/Library/Application Support/...`, `%APPDATA%\...`, e.g. via
   `platformdirs`), keep today's folders working for developers, and offer an
   "Open data folder" button.
2. **Errors in tile requests** must come back as an error response, never a
   dropped connection (seen in the trial when orthophoto tiles failed).
3. **iPhone transfer inside the app:** replace the `afcclient` calls in
   `drone/iphone.py` with `pymobiledevice3` (pure Python). Windows still needs
   Apple's device drivers (iTunes or the Apple Devices app).
4. **Own window (optional):** `pywebview` shows the planner in an application
   window instead of a browser tab; closing it quits the server.
5. **Automatic builds:** a GitHub Actions workflow builds Windows, macOS (Apple
   Silicon and Intel) and Linux versions when a version is tagged, runs a smoke
   test of the built program (start it, load the page, plan an area, load a
   small orthophoto), and attaches the downloads to a GitHub release.
   PyInstaller can't build for another system than the one it runs on.
6. **Olympe features** (pack check, laptop flying) stay out of the packaged app:
   it shows them greyed out with an explanation, as it does today without
   Olympe. Our own small client for Parrot's protocol (see "Battery check on
   every computer" in the checklist) would bring them in later.
7. **Signing, decided later:** macOS blocks unsigned apps (users can right-click
   → Open the first time; notarization needs an Apple Developer account,
   US$99/year); Windows shows a SmartScreen warning without a code-signing
   certificate; Linux has no such issue. Unsigned with a short README note is
   common for free tools.
8. **Installers (optional, later):** Briefcase (BeeWare) can build `.msi` and
   `.dmg` installers from the same code if a plain download isn't enough.
