#!/usr/bin/env python3
"""Build a clean universal2 app and DMG. Run with a universal2 Python + PyInstaller."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile

VERSION = '1.0.0'
ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kw):
    subprocess.run([str(x) for x in args], check=True, **kw)


def build(destination):
    # Source allowlist deliberately excludes plans, private .ai records and test media.
    scratch = Path(tempfile.mkdtemp(prefix='lightharbor-build-', dir='/private/tmp'))
    source = scratch / 'source'; source.mkdir()
    for name in ('app.py', 'planner.py', 'mac_entry.py', 'index.html', 'MediaProbe.swift'):
        shutil.copy2(ROOT / name, source / name)
    env = os.environ.copy()
    cache = Path(env.get('LIGHTHARBOR_MODULE_CACHE', str(scratch / 'module-cache')))
    env['PYINSTALLER_CONFIG_DIR'] = str(scratch / 'pyinstaller-cache')
    env['CLANG_MODULE_CACHE_PATH'] = str(cache)
    env['SWIFT_MODULECACHE_PATH'] = str(cache)
    for arch in ('arm64', 'x86_64'):
        for name, src in [('media-probe', source / 'MediaProbe.swift'), ('LightHarbor', ROOT / 'macos/Launcher.swift')]:
            run('xcrun', 'swiftc', '-O', '-target', f'{arch}-apple-macos13.0', '-module-cache-path', cache, src, '-o', scratch / f'{name}-{arch}', env=env)
    run('lipo', '-create', scratch / 'media-probe-arm64', scratch / 'media-probe-x86_64', '-output', source / 'media-probe')
    run(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--console', '--noupx', '--target-arch', 'universal2', '--name', 'lightharbor-engine', '--distpath', scratch / 'engine-dist', '--workpath', scratch / 'freeze', '--specpath', scratch, '--add-data', str(source / 'index.html') + ':.', '--add-binary', str(source / 'media-probe') + ':.', '--exclude-module', 'tkinter', '--exclude-module', 'unittest', '--exclude-module', 'pydoc', 'mac_entry.py', cwd=source, env=env)
    stage = scratch / 'share'; stage.mkdir()
    application = stage / '拾光.app'; contents = application / 'Contents'
    (contents / 'MacOS').mkdir(parents=True); resources = contents / 'Resources'; resources.mkdir()
    run('lipo', '-create', scratch / 'LightHarbor-arm64', scratch / 'LightHarbor-x86_64', '-output', contents / 'MacOS/LightHarbor')
    shutil.copytree(scratch / 'engine-dist/lightharbor-engine', resources / 'engine', symlinks=True)
    run('xcrun', 'swift', '-module-cache-path', cache, ROOT / 'macos/make_icon.swift', scratch / 'icon.png', env=env)
    iconset = scratch / 'LightHarbor.iconset'; iconset.mkdir()
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            name = f'icon_{size}x{size}' + ('@2x' if scale == 2 else '') + '.png'
            run('sips', '-z', size * scale, size * scale, scratch / 'icon.png', '--out', iconset / name, stdout=subprocess.DEVNULL)
    run('iconutil', '-c', 'icns', iconset, '-o', resources / 'LightHarbor.icns')
    info = {'CFBundleName': '拾光', 'CFBundleDisplayName': '拾光 · LightHarbor', 'CFBundleExecutable': 'LightHarbor', 'CFBundleIdentifier': 'io.github.yunqingzhou9-spec.lightharbor', 'CFBundleVersion': '1', 'CFBundleShortVersionString': VERSION, 'CFBundlePackageType': 'APPL', 'CFBundleIconFile': 'LightHarbor', 'LSMinimumSystemVersion': '13.0', 'NSHighResolutionCapable': True, 'NSPrincipalClass': 'NSApplication', 'NSRemovableVolumesUsageDescription': '读取你选择的存储卡照片，并复制到你选择的归档目录。', 'NSDocumentsFolderUsageDescription': '读取或保存你选择的照片归档。', 'NSDownloadsFolderUsageDescription': '读取你选择的下载文件夹中的照片。', 'NSDesktopFolderUsageDescription': '读取或保存你选择的桌面照片归档。'}
    (contents / 'Info.plist').write_bytes(plistlib.dumps(info))
    licenses = resources / 'ThirdPartyLicenses'; licenses.mkdir()
    python_license = Path(sys.base_prefix) / 'lib/python3.14/LICENSE.txt'
    if not python_license.exists():
        python_license = Path(sys.base_prefix) / f'lib/python{sys.version_info.major}.{sys.version_info.minor}/LICENSE.txt'
    shutil.copy2(python_license, licenses / 'Python-LICENSE.txt')
    for license_file in (ROOT / 'macos/thirdparty').glob('*.txt'):
        shutil.copy2(license_file, licenses / license_file.name)
    for package in ('pyinstaller', 'altgraph', 'macholib', 'packaging', 'setuptools', 'pyinstaller-hooks-contrib'):
        dist = importlib.metadata.distribution(package)
        files = [x for x in dist.files or [] if any(part.lower().startswith(('license', 'copying')) for part in x.parts)]
        for i, file in enumerate(files):
            path = Path(dist.locate_file(file))
            if path.is_file(): shutil.copy2(path, licenses / f'{package}-{i}-{path.name}')
    (licenses / 'NOTICE.txt').write_text('Runtime: Python Software Foundation Python 3.14.7. Packaged with PyInstaller 6.22.3 under its bootloader distribution exception. Native system frameworks are supplied by macOS. No photo data or previous user settings are included.\n', encoding='utf-8')
    shutil.copy2(ROOT / 'macos/使用说明.txt', stage / '先读我.txt')
    os.symlink('/Applications', stage / 'Applications')
    run('codesign', '--force', '--deep', '--sign', '-', '--timestamp=none', application)
    run('codesign', '--verify', '--deep', '--strict', '--verbose=2', application)
    destination.mkdir(parents=True, exist_ok=True)
    dmg = destination / f'拾光-{VERSION}-macOS-universal.dmg'
    run('hdiutil', 'create', '-volname', f'LightHarbor {VERSION}', '-srcfolder', stage, '-format', 'UDZO', '-ov', dmg)
    digest = hashlib.sha256(dmg.read_bytes()).hexdigest()
    (destination / 'SHA256SUMS.txt').write_text(f'{digest}  {dmg.name}\n', encoding='utf-8')
    print(json.dumps({'dmg': str(dmg), 'application': str(application), 'scratch': str(scratch), 'sha256': digest}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, default=ROOT / 'release')
    build(parser.parse_args().output.resolve())
