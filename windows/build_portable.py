"""Assemble a clean Windows x64 preview from pinned cached vendor artifacts.
This is packaging, not Windows native execution or cross-compilation.
"""
import argparse
import base64
import csv
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from version import VERSION
SHARED_VERSION = VERSION
SOURCES = ('version.py', 'app.py', 'planner.py', 'copy_workflow.py', 'organizer_insights.py',
           'card_cleanup.py', 'windows_media.py', 'windows_entry.py', 'index.html',
           'windows_folder_picker.py', 'windows_folder_picker.html')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checked_members(archive):
    names = set()
    for item in archive.infolist():
        path = PurePosixPath(item.filename)
        if path.is_absolute() or '..' in path.parts or '\\' in item.filename or ':' in item.filename or stat.S_ISLNK(item.external_attr >> 16) or item.filename in names:
            raise ValueError('Unsafe or duplicate archive member: ' + item.filename)
        names.add(item.filename)
        yield item


def adapt_frontend(html):
    old = '版本 @@VERSION@@'
    if html.count(old) != 1:
        raise ValueError('Unexpected shared UI version')
    # The shared HTTP handler resolves the single development version at runtime.
    old_basename = "const lhBasename=value=>(value||'').replace(/\\/+$/,'').split('/').pop()||'';"
    new_basename = "const lhBasename=value=>(value||'').replaceAll('\\\\','/').replace(/\\/+$/,'').split('/').pop()||'';"
    if html.count(old_basename) != 1:
        raise ValueError('Unexpected basename function')
    html = html.replace(old_basename, new_basename)
    html = html.replace('景物 · 离线识别画面', '景物识别 · Windows 版暂不支持').replace('在访达中打开', '在文件资源管理器中打开')
    html = html.replace("(state.destination||'').split('/')", "(state.destination||'').replaceAll('\\\\','/').split('/')")
    # Compatibility only: old hidden scene setting cannot enable an unsupported model.
    html = html.replace('</script>', "\nconst winRender=render;render=function(force=false){winRender(force);const scene=$('scene');if(scene){scene.checked=false;scene.disabled=true;}};\n</script>")
    return html


def build(inputs, work, output):
    pins = json.loads((ROOT / 'windows/dependencies.json').read_text())
    artifacts = [pins['python'], *pins['wheels']]
    for item in artifacts:
        file = inputs / ('wheels314' if item in pins['wheels'] else '') / item['file']
        if digest(file) != item['sha256']:
            raise ValueError('Dependency hash mismatch: ' + item['file'])
    for item in pins.get('sources', []):
        if digest(inputs / 'sources' / item['file']) != item['sha256']:
            raise ValueError('Vendor source hash mismatch: ' + item['file'])
    stage = work / f'LightHarbor-Windows-{VERSION}-preview'
    archive = output / f'LightHarbor-{VERSION}-preview-Windows-x64.zip'
    if stage.exists() or archive.exists() or archive.with_suffix('.sha256').exists():
        raise ValueError('Output already exists; use a fresh work/output directory')
    stage.mkdir(parents=True)
    runtime = stage / 'runtime'; runtime.mkdir()
    with zipfile.ZipFile(inputs / pins['python']['file']) as z:
        for item in checked_members(z):
            z.extract(item, runtime)
    site = runtime / 'Lib/site-packages'; site.mkdir(parents=True)
    for wheel in pins['wheels']:
        with zipfile.ZipFile(inputs / 'wheels314' / wheel['file']) as z:
            members = list(checked_members(z))
            record = next(name for name in z.namelist() if name.endswith('.dist-info/RECORD'))
            for name, checksum, size in csv.reader(io.StringIO(z.read(record).decode())):
                if checksum:
                    algorithm, expected = checksum.split('=', 1)
                    actual = base64.urlsafe_b64encode(hashlib.new(algorithm, z.read(name)).digest()).decode().rstrip('=')
                    if actual != expected or size and len(z.read(name)) != int(size):
                        raise ValueError('Wheel RECORD mismatch: ' + name)
            for item in members:
                if item.is_dir():
                    continue
                relative = PurePosixPath(item.filename)
                if '.data/' in item.filename:
                    if '.data/scripts/' in item.filename:
                        continue
                    # Wheel installation relocates platlib DLLs beside packages.
                    parts = relative.parts
                    if len(parts) < 2 or parts[1] not in ('platlib', 'purelib'):
                        raise ValueError('Unexpected wheel data scheme: ' + item.filename)
                    if len(parts) == 2:
                        continue
                    relative = PurePosixPath(*parts[2:])
                target = site / relative
                if target.is_file():
                    raise ValueError('Wheel file collision: ' + item.filename)
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(z.read(item))
    (runtime / 'python314._pth').write_text('python314.zip\n.\nLib/site-packages\n../app\nimport site\n', encoding='ascii')
    application = stage / 'app'; application.mkdir()
    for name in SOURCES:
        shutil.copy2(ROOT / name, application / name)
    (application / 'index.html').write_text(adapt_frontend((ROOT / 'index.html').read_text()), encoding='utf-8')
    (stage / 'Start-LightHarbor.cmd').write_bytes(b'@echo off\r\nsetlocal\r\ncd /d "%~dp0"\r\n"%~dp0runtime\\python.exe" "%~dp0app\\windows_entry.py"\r\nif errorlevel 1 pause\r\n')
    shutil.copy2(ROOT / 'windows/先读我.txt', stage / '先读我.txt')
    shutil.copytree(ROOT / 'windows/thirdparty', stage / 'ThirdPartyLicenses')
    vendor_sources = stage / 'ThirdPartySources'
    vendor_sources.mkdir()
    for item in pins.get('sources', []):
        shutil.copy2(inputs / 'sources' / item['file'], vendor_sources / item['file'])
    (stage / 'DEPENDENCIES.json').write_text(json.dumps(pins, indent=2), encoding='utf-8')
    (stage / 'SOURCE_MANIFEST.json').write_text(json.dumps({'version': VERSION, 'synced_mac': SHARED_VERSION, 'files': [{'path': name, 'source_sha256': digest(ROOT / name), 'packaged_sha256': digest(application / name)} for name in SOURCES]}, indent=2), encoding='utf-8')
    (stage / 'PACKAGE.json').write_text(json.dumps({'version': VERSION, 'platform': 'Windows x64', 'status': 'preview', 'windows_native_validation': 'not performed', 'system_components': ['Microsoft Edge WebView2 Runtime', '.NET Framework 4.8'], 'no_administrator_required': True}, indent=2), encoding='utf-8')
    (stage / 'FILES.json').write_text(json.dumps({str(p.relative_to(stage)).replace('\\', '/'): digest(p) for p in sorted(stage.rglob('*')) if p.is_file()}, indent=2), encoding='utf-8')
    output.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
        for file in sorted(stage.rglob('*')):
            if file.is_file():
                z.write(file, PurePosixPath(stage.name) / file.relative_to(stage))
    checksum = digest(archive)
    archive.with_suffix('.sha256').write_text(checksum + '  ' + archive.name + '\n', encoding='ascii')
    print(json.dumps({'archive': str(archive), 'stage': str(stage), 'bytes': archive.stat().st_size, 'sha256': checksum}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--inputs', type=Path, default=ROOT.parents[1] / 'work/windows-package')
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'release')
    args = parser.parse_args()
    build(args.inputs.resolve(), args.work.resolve(), args.output.resolve())
