"""Assemble Windows x64 portable preview from official embedded Python and wheels.
No cross-compilation: Windows binaries are vendor-supplied distribution artifacts.
"""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

ROOT=Path(__file__).resolve().parents[1]
WORK=Path.cwd()/'work/windows-package'
STAGE=WORK/'LightHarbor-Windows-1.4.1-preview'
if STAGE.exists():raise SystemExit('Staging directory already exists; choose a fresh directory')
STAGE.mkdir(parents=True)
runtime=STAGE/'runtime';runtime.mkdir()
with zipfile.ZipFile(WORK/'python-3.14.7-embed-amd64.zip') as z:z.extractall(runtime)
site=runtime/'Lib/site-packages';site.mkdir(parents=True)
manifest=[]
for wheel in sorted((WORK/'wheels314').glob('*.whl')):
    with zipfile.ZipFile(wheel) as z:
        for name in z.namelist():
            if '.data/' in name:
                # Console entrypoints are not used by the desktop application.
                if '.data/scripts/' in name: continue
                raise ValueError('Unexpected wheel data scheme: '+name)
            z.extract(name,site)
    manifest.append({'file':wheel.name,'sha256':hashlib.sha256(wheel.read_bytes()).hexdigest()})
(runtime/'python314._pth').write_text('python314.zip\n.\nLib/site-packages\n../app\nimport site\n',encoding='ascii')
application=STAGE/'app';application.mkdir()
for name in ('app.py','planner.py','copy_workflow.py','organizer_insights.py', 'card_cleanup.py','windows_media.py','windows_entry.py','index.html'):
    shutil.copy2(ROOT/name,application/name)
html=(application/'index.html').read_text()
html=html.replace('景物 · 离线识别画面','景物识别 · Windows 版暂不支持').replace('在访达中打开','在文件资源管理器中打开')
html=html.replace("(state.destination||'').split('/')", "(state.destination||'').replaceAll('\\\\','/').split('/')")
html=html.replace('</script>',"\nconst winRender=render;render=function(force=false){winRender(force);$('scene').checked=false;$('scene').disabled=true;};\n</script>")
(application/'index.html').write_text(html,encoding='utf-8')
(STAGE/'Start-LightHarbor.cmd').write_bytes(b'@echo off\r\nsetlocal\r\ncd /d "%~dp0"\r\n"%~dp0runtime\\python.exe" "%~dp0app\\windows_entry.py"\r\nif errorlevel 1 pause\r\n')
shutil.copy2(ROOT/'windows/先读我.txt',STAGE/'先读我.txt')
(STAGE/'DEPENDENCIES.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
archive=ROOT/'release/LightHarbor-1.4.1-preview-Windows-x64.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
    for f in sorted(STAGE.rglob('*')):
        if f.is_file():z.write(f,Path(STAGE.name)/f.relative_to(STAGE))
checksum=hashlib.sha256(archive.read_bytes()).hexdigest()
archive.with_suffix('.sha256').write_text(checksum+'  '+archive.name+'\n')
print(json.dumps({'archive':str(archive),'bytes':archive.stat().st_size,'sha256':checksum},ensure_ascii=False))
