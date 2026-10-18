# macOS distribution build

The app is a native AppKit launcher around a bundled local Python service. The editing workspace is embedded in a system WKWebView with origin-scoped native folder and clipboard bridges. Runtime data and tokens live only in memory; no source card or developer plan is shipped.

Requirements: macOS, Xcode Command Line Tools, a **universal2** Python 3.14.7 from python.org, PyInstaller 6.22.3 (plus its pinned build dependencies). Use a dedicated virtual environment. The build script does not install or download anything.

```sh
python3 -m pip install pyinstaller==6.22.3 altgraph==0.17.5 macholib==1.16.4 packaging==26.3 pyinstaller-hooks-contrib==2026.7 setuptools==84.0.0
python3 macos/build_mac.py
```

The script compiles the launcher and ImageIO/Vision helper for arm64 and x86_64 with a macOS 13 deployment target, packages Python, generates a local vector-based icon, signs the bundle ad hoc, then creates a ZIP and SHA-256 checksum. No browser is launched. Source staging uses an explicit five-file allowlist. Build records, `.ai`, repository metadata, tests and photos are never part of the runtime package.

A universal2 build validates binary slices, but this is not a claim of native testing on every Intel Mac/macOS version. Test each release from an extracted/relocated app without developer environment variables, including EXIF/thumbnail handling and a fixture-only copy with hash comparison. The launcher uses private stdin to request safe shutdown, waits for active work to stop, and blocks exit until the child service has ended. Unexpected launcher death closes stdin and requests the same cleanup.

The produced app has **no Developer ID certificate or Apple notarization**. Public releases must disclose this; ad-hoc signature verification is not notarization. For trusted general distribution, rebuild/sign all nested binaries with the distributor's valid Developer ID, submit to Apple notarization, staple and verify before replacing the release. Never disable Gatekeeper in installation scripts.
