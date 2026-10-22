#!/usr/bin/env python3
"""Local, copy-only media organizer. Python standard library + macOS helper."""
import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import secrets
import select
import shutil
import stat
import socketserver
import subprocess
import threading
import time
import webbrowser
import unicodedata
import planner
from version import VERSION, DISPLAY_VERSION
from copy_workflow import CopyWorkflow, data_directory
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = Path(__file__).resolve().parent
IMAGES = set('jpg jpeg heic heif png tif tiff webp gif bmp avif'.split())
RAW = set('cr2 cr3 nef nrw arw raf dng orf rw2 pef srw'.split())
VIDEOS = set('mp4 mov m4v avi mkv mts m2ts 3gp webm'.split())
def kind(path):
    ext = path.suffix.lower().lstrip('.')
    if ext in IMAGES: return '照片'
    if ext in RAW: return 'RAW原片'
    if ext in VIDEOS: return '视频'
    if ext in {'wav', 'mp3', 'aac', 'flac', 'm4a'}: return '音频'
    if ext in {'xmp', 'aae', 'thm', 'lrv'}: return '伴随文件'
    return '其他文件'


def fingerprint(path):
    s = path.lstat()
    if not stat.S_ISREG(s.st_mode): raise ValueError('不是普通文件，已跳过')
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]


def valid_roots(source, destination):
    source = Path(source).expanduser().resolve(strict=True)
    destination = Path(destination).expanduser().resolve()
    if not source.is_dir(): raise ValueError('来源必须是文件夹')
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError('来源和目标不能相同，也不能互相包含；请选择独立的目标文件夹')
    if destination.exists() and not destination.is_dir(): raise ValueError('目标不是文件夹')
    return source, destination


def valid_sources(sources, destination):
    if not isinstance(sources, list) or not sources or len(sources) > 100:
        raise ValueError('请选择 1–100 个来源文件夹')
    roots, dest = [], None
    for value in sources:
        if not isinstance(value, str) or not value.strip(): raise ValueError('来源路径无效')
        root, dest = valid_roots(value, destination)
        if root not in roots: roots.append(root)
    # Parent roots cover selected descendants. The UI keeps the original list.
    return [r for r in roots if not any(p in r.parents for p in roots)], dest


def row_source_root(row, sources):
    path = Path(row['source'])
    if path.resolve() != path: raise ValueError('来源路径已改变')
    roots = [Path(p) for p in sources]
    root = next((r for r in roots if r in path.parents), None)
    if root is None: raise ValueError('文件不属于所选来源')
    return root


class Probe:
    def __init__(self):
        self.proc = None
        helper = Path(os.environ.get('LIGHTHARBOR_MEDIA_PROBE', str(BASE / 'media-probe')))
        if os.name != 'nt' and helper.is_file():
            try:
                self.proc = subprocess.Popen([str(helper)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
            except OSError: pass

    def read(self, path, scene):
        if os.name == 'nt':
            from windows_media import metadata
            return metadata(path, False)
        if not self.proc: return {'warning': '图片读取组件不可用；使用文件修改日期'}
        try:
            self.proc.stdin.write(json.dumps({'path': str(path)}) + '\n')
            self.proc.stdin.flush()
            if not select.select([self.proc.stdout], [], [], 45)[0]:
                self.close()
                return {'warning': '拍摄时间读取超时；后续图片使用文件修改日期'}
            return json.loads(self.proc.stdout.readline())
        except (OSError, ValueError):
            self.close()
            return {'warning': '图片读取组件异常；使用文件修改日期'}

    def close(self):
        if self.proc:
            self.proc.kill()
            self.proc.wait()
            self.proc.stdin.close()
            self.proc.stdout.close()
            self.proc = None


def scan(source, destination, options, progress=lambda *_: None, cancelled=lambda: False):
    if options.get('cleanup_only'):
        from card_cleanup import source_card_guard
        source, destination = source_card_guard(source), Path(destination).expanduser().resolve()
    else:
        source, destination = valid_roots(source, destination)
    rows, warnings = [], []
    probe = Probe()
    try:
        def walk_error(error): warnings.append(str(error))
        for folder, dirs, files in os.walk(source, followlinks=False, onerror=walk_error):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and not Path(folder, d).is_symlink())
            for name in sorted(files):
                if cancelled(): return rows, warnings
                p = Path(folder, name)
                if name.startswith('.') or p.is_symlink(): continue
                try:
                    fp = fingerprint(p)
                    media_kind = kind(p)
                    meta = probe.read(p, False) if media_kind in ('照片', 'RAW原片') else {}
                    date, date_source = dt.datetime.fromtimestamp(fp[3] / 1e9), '文件修改日期'
                    if meta.get('date'):
                        try:
                            date = dt.datetime.strptime(meta['date'][:19], '%Y:%m:%d %H:%M:%S')
                            date_source = '拍摄日期'
                        except ValueError: pass
                    reliable = date_source == '拍摄日期' and bool(meta.get('reliable_time', True))
                    if reliable and str(meta.get('subsecond', '')).isdigit():
                        date = date.replace(microsecond=int((str(meta['subsecond']) + '000000')[:6]))
                    # Compatibility fields remain inert for saved-plan readers. New scans
                    # never extract or analyze location/scene, even with stale settings.
                    place, scene, confidence = '位置未知', '未启用', 0
                    parts = [date.strftime('%Y'), date.strftime('%Y-%m-%d'), media_kind]
                    rows.append({'source': str(p), 'source_root':str(source), 'source_label':source.name, 'relative': p.relative_to(source).as_posix(), 'target': Path(*parts, name).as_posix(), 'size': fp[2], 'fingerprint': fp, 'date_source': date_source, 'date':date.strftime('%Y-%m-%d'), 'capture_time':date.isoformat(), 'reliable_time':reliable, 'camera':meta.get('camera',''), 'kind':media_kind, 'location': place, 'scene': scene, 'confidence': round(confidence, 3), 'warning': meta.get('warning', '')})
                    progress(len(rows), str(p.relative_to(source)))
                except (OSError, ValueError) as exc: warnings.append(f'{p.relative_to(source)}：{exc}')
    finally: probe.close()
    return rows, warnings


def digest(path, cancelled=lambda: False):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(1024 * 1024):
            if cancelled(): raise InterruptedError('已停止')
            h.update(chunk)
    return h.hexdigest()


def safe_folder(root, relative):
    current = root
    for part in relative.parts:
        if part in ('..', '.', ''): raise ValueError('目标路径无效')
        current = current / part
        try: current.mkdir()
        except FileExistsError: pass
        if current.is_symlink() or not current.is_dir() or root not in current.resolve().parents:
            raise ValueError('目标目录包含符号链接或路径异常')
    return current


def copy_one(row, source_root, destination, cancelled=lambda: False, exact=False, allow_duplicate=True, progress=lambda amount: None):
    source = Path(row['source'])
    if source.is_symlink() or source.resolve() != source or source_root not in source.parents:
        raise ValueError('来源路径已改变')
    if fingerprint(source) != row['fingerprint']: raise ValueError('文件扫描后已变化，请重新扫描')
    relative = Path(row['target'])
    if relative.is_absolute() or '..' in relative.parts: raise ValueError('目标路径无效')
    parent = safe_folder(destination, relative.parent) if relative.parent != Path('.') else destination
    expected = digest(source, cancelled) if allow_duplicate else None
    streaming_hash = hashlib.sha256()
    if fingerprint(source) != row['fingerprint']: raise ValueError('读取时来源发生变化')
    original = parent / relative.name
    counter = 0
    while True:
        target = original if counter == 0 else original.with_name(f'{original.stem}__{counter}{original.suffix}')
        counter += 1
        try:
            writer = target.open('xb')
            break
        except FileExistsError:
            if allow_duplicate and not target.is_symlink() and target.is_file() and target.stat().st_size == row['size'] and digest(target, cancelled) == expected:
                return {'status': '重复跳过', 'target': str(target), 'sha256': expected}
            if exact: raise ValueError('目标在确认后发生冲突，请重新检查计划')
    try:
        with writer, source.open('rb') as reader:
            while chunk := reader.read(1024 * 1024):
                if cancelled(): raise InterruptedError('已停止')
                writer.write(chunk)
                streaming_hash.update(chunk)
                progress(len(chunk))
            writer.flush()
            os.fsync(writer.fileno())
        if expected is None: expected = streaming_hash.hexdigest()
        if fingerprint(source) != row['fingerprint'] or digest(target, cancelled) != expected:
            raise ValueError('复制校验失败或来源发生变化')
        os.utime(target, ns=(row['fingerprint'][3], row['fingerprint'][3]))
        return {'status': '已复制', 'target': str(target), 'sha256': expected}
    except BaseException:
        target.unlink(missing_ok=True)
        raise


class Organizer(CopyWorkflow):
    def __init__(self, store=None):
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.rows = []
        self.plan = None
        self.history = []
        self.revision = 0
        self.source = self.destination = None
        self.sources = []
        self.state = {'phase':'idle', 'count':0, 'current':'', 'warnings':[], 'results':[], 'version':3}
        self.init_workflow(store)

    def snapshot(self):
        with self.lock:
            rows = self.state.get('results', [])
            result = copy.deepcopy({key: value for key, value in self.state.items() if key != 'results'})
            result['result_counts'] = {status: sum(row.get('status') == status for row in rows)
                                       for status in ('已复制', '重复跳过', '失败')}
            result['processed'] = len(rows)
            result['remaining'] = max(0, self.state.get('included', 0) - len(rows))
            result['results'] = copy.deepcopy(rows[-1000:])
            result['results_total'] = len(rows)
            result['saved_tasks'] = copy.deepcopy(self.saved_tasks)
            result['persistence'] = self.store is not None
            result['transfer'] = self.transfer_snapshot()
            result['completion'] = self.completion_summary(rows) if result.get('phase') in ('done','cancelled','error') else None
            result['sources']=[str(p) for p in self.sources] if self.sources else ([str(self.source)] if self.source else [])
            result['other_items']=[{'id':sid,'name':Path(self.plan['rows'][shot['rows'][0]]['source']).name,'kind':self.plan['rows'][shot['rows'][0]]['kind'],'source_root':self.plan['rows'][shot['rows'][0]].get('source_root',''),'date':shot['date'],'target':self.plan['rows'][shot['rows'][0]]['target'],'selected':sid in self.active_shots()} for sid,shot in self.plan['shots'].items() if not shot['photo']] if self.plan else []
            if result['completion']:
                result['completion'].update(phase=self.state['phase'],planned=self.state.get('included',0),unprocessed=result['remaining'])
            result['import_overview'] = self.import_overview() if self.plan and 'rows' in self.plan else []
            return result

    def result_page(self, payload):
        with self.lock:
            self._check(payload, ('copying', 'paused', 'done', 'cancelled', 'error'))
            offset = payload.get('offset', 0)
            if type(offset) is not int or offset < 0:
                raise ValueError('明细起始位置无效')
            rows = self.state.get('results', [])
            return {'results': copy.deepcopy(rows[offset:offset + 200]), 'total': len(rows)}

    def _publish(self):
        active = self.active_rows()
        selected = self.active_shots()
        self.state['scope_groups'] = {g['id']: sum(len(self.plan['shots'][sid]['rows']) for sid in g['shots'] if sid in selected) for g in self.plan['groups']}
        self.state.update(plan_id=self.plan['id'], revision=self.plan['revision'], groups=planner.summaries(self.plan), count=len(self.rows), included=len(active), excluded=sum(bool(r['excluded']) for r in self.plan['rows'].values()), scope=self.plan.get('scope', {'mode':'all','shots':[]}), scope_shots=len(self.active_shots()), outside_scope=len(self.rows)-len(active)-sum(bool(r['excluded']) for r in self.plan['rows'].values()), total_bytes=sum(r['size'] for r in active), can_undo=bool(self.history), source=str(self.source), destination=str(self.destination), options=self.plan['options'])

        dates={}
        selected=self.active_shots()
        for group in self.plan['groups']:
            for sid in group['shots']:
                shot=self.plan['shots'][sid]
                if not shot['photo']:continue
                entry=dates.setdefault(shot['date'],{'date':shot['date'],'shots':0,'files':0,'selected':0,'groups':{}})
                entry['shots']+=1;entry['files']+=len(shot['rows']);entry['selected']+=int(sid in selected and not shot['excluded'])
                entry['groups'][group['id']]=entry['groups'].get(group['id'],0)+1
        self.state['photo_dates']=sorted(dates.values(),key=lambda d:d['date'],reverse=True)
        self.state['catalog_revision']=planner.catalog_revision(self.plan)
        self.state['copy_format']=self.plan.get('copy_format','both')
        self.state['scope_photo_shots']=sum(self.plan['shots'][sid]['photo'] for sid in selected)
        self.state['scope_other_items']=sum(not self.plan['shots'][sid]['photo'] for sid in selected)
        active_photo_ids={r['shot'] for r in active}
        self.state['copy_skipped_shots']=sum(1 for sid in selected if self.plan['shots'][sid]['photo'] and sid not in active_photo_ids)

    def _check(self, payload, phases=('ready','reviewing')):
        if self.state['phase'] not in phases or not self.plan: raise ValueError('当前不能调整或确认，请等待扫描完成')
        if payload.get('plan_id') != self.plan['id'] or payload.get('revision') != self.plan['revision']:
            raise ValueError('预览已更新，请刷新后重试；未执行操作')

    def start_scan(self, payload):
        with self.lock:
            if self.state['phase'] in ('scanning','preparing','copying','paused'): raise ValueError('请等待当前任务完成')
            requested = payload.get('sources') or [payload.get('source')]
            if not payload.get('destination'):raise ValueError('请填写来源和目标路径')
            options=dict(payload.get('options',{}))
            # This policy applies only to new scans. Saved tasks retain their options.
            options.update(mode='groups', date=True, type=True, separate_timelapse=True, location=False, scene=False, date_only=True)
            if options.get('cleanup_only'):
                if len(requested) != 1: raise ValueError('清理仅支持一个来源文件夹')
                from card_cleanup import source_card_guard
                source,dest=source_card_guard(requested[0]),Path(payload['destination']).expanduser().resolve()
                roots = [source]
            else:
                roots,dest=valid_sources(requested,payload['destination']); source=roots[0]
                options.update(layout_version=2, duplicate_mode='content')
                if type(options.get('separate_formats', True)) is not bool: raise ValueError('格式分离设置无效')
                options.setdefault('separate_formats', True)
            if options.get('duplicate_mode','name') not in ('name','content'):raise ValueError('查重方式无效')
            minimum=int(options.get('min_frames',30));tolerance=float(options.get('tolerance',20))
            if not 3<=minimum<=100000 or not 5<=tolerance<=60:raise ValueError('连续张数 3–100000，间隔容差 5–60%')
            previous = copy.deepcopy(self.plan) if self.plan and not options.get('cleanup_only') and not self.plan['options'].get('cleanup_only') else None
            self.source,self.sources,self.destination,self.rows=source,roots,dest,[]
            self.plan=None;self.history=[];self.cancel.clear();self.init_insights()
            self.state={'phase':'scanning','scan_stage':'reading','count':0,'current':'','warnings':[],'results':[],'version':3,'source':str(source),'sources':[str(r) for r in roots],'destination':str(dest),'options':options}
        threading.Thread(target=self._scan,args=(options,previous),daemon=True).start()

    def _scan(self,options,previous=None):
        try:
            def progress(n,name):
                with self.lock:self.state.update(count=n,current=name)
            rows,warnings=[],[]
            for root in self.sources or [self.source]:
                base=len(rows)
                found,issues=scan(root,self.destination,options,lambda n,name:progress(base+n,root.name+' / '+name),self.cancel.is_set)
                rows.extend(found);warnings.extend(issues)
                if self.cancel.is_set(): break
            with self.lock:self.state.update(scan_stage='grouping',current='正在整理日期与拍摄组')
            plan=planner.build_plan(rows,options)
            if previous: planner.reconcile_scan(plan,previous)
            plan['sources']=[str(r) for r in self.sources]
            with self.lock:
                self.rows=rows;self.plan=plan;self.revision=1
            if not options.get("cleanup_only"):
                self.apply_archive_layout()
                with self.lock:self.state.update(scan_stage='imports',current='正在检查已有副本')
                self.assess_imports()
            with self.lock:
                final_phase='cancelled' if self.cancel.is_set() else 'ready'
                self.state.update(warnings=warnings,metadata_warnings=sum(bool(r['warning']) for r in rows),fallback_dates=sum(not r['reliable_time'] for r in rows))
                self._publish(); self.save_task(phase=final_phase)
                self.state['phase']=final_phase
        except Exception as exc:
            with self.lock:self.state.update(phase='error',error=str(exc))

    def edit(self,payload):
        with self.lock:
            self._check(payload)
            if payload.get('action') in ('move','merge','rename','split','kind'):
                raise ValueError('已移除拍摄组调整；请在照片列表中修正所选照片类型')
            if payload.get('action')=='undo':
                if not self.history:raise ValueError('没有可撤销的调整')
                checkpoint=self.history.pop()
                self.plan['groups']=checkpoint['groups']
                for sid,excluded in checkpoint['excluded'].items():self.plan['shots'][sid]['excluded']=excluded
            else:
                if payload.get('action')=='photo_kind':
                    if 'shot' in payload:
                        sid=payload['shot']
                        if not isinstance(sid,str) or sid not in self.plan['shots'] or not self.plan['shots'][sid]['photo']:
                            raise ValueError('照片已失效，请重新查看照片列表')
                        # An explicit per-photo correction never reads or changes copy scope.
                        payload=dict(payload,shots=[sid])
                    else:
                        matches=set(planner.matching_photo_ids(self.plan,payload.get('filter',{})))
                        chosen=matches & self.active_shots()
                        if not chosen:raise ValueError('请先选择当前浏览范围内的照片')
                        payload=dict(payload,shots=sorted(chosen))
                if payload.get('all_group') and payload.get('action')!='photo_kind':
                    g=next((g for g in self.plan['groups'] if g['id']==payload.get('group')),None)
                    if not g:raise ValueError('拍摄组不存在')
                    payload=dict(payload,shots=g['shots'])
                result=planner.edit(self.plan,payload)
                self.history.append({'groups':copy.deepcopy(self.plan['groups']),'excluded':{sid:s['excluded'] for sid,s in self.plan['shots'].items()}});self.history=self.history[-12:]
                self.plan=result
            self.revision+=1;self.plan['revision']=self.revision
            # Undo always invalidates prior preflight too.
            planner.refresh(self.plan)
            self.state.update(phase='ready',preflight=None,error='',results=[])
            self._publish(); self.save_task()

    def files(self,payload):
        with self.lock:
            self._check(payload,('ready','reviewing','done','copying','paused','cancelled'))
            date=payload.get('date')
            if date is not None and (not isinstance(date,str) or date not in {s['date'] for s in self.plan['shots'].values() if s['photo']}):raise ValueError('拍摄日期无效')
            return planner.page(self.plan,payload.get('group'),int(payload.get('page',0)),date=date)

    def photo_catalog(self,payload):
        with self.lock:
            self._check(payload,('ready','reviewing','done','copying','paused','cancelled'))
            return {'shots':planner.photo_catalog(self.plan,payload.get('filter',{}))}

    def preferences(self,payload):
        if not self.store:return {}
        path=self.store.parent/'organizer-preferences.json'
        with self.lock:
            if 'values' in payload:
                values=payload['values'];clean={}
                if not isinstance(values,dict):raise ValueError('设置无效')
                for key in ('detect','separate_formats'):
                    if key in values:
                        if type(values[key]) is not bool:raise ValueError('设置无效')
                        clean[key]=values[key]
                for key,allowed in [('duplicate_mode',('name','content'))]:
                    if key in values:
                        if values[key] not in allowed:raise ValueError('设置无效')
                        clean[key]=values[key]
                for key,low,high in [('min_frames',3,100000),('tolerance',5,60)]:
                    if key in values:
                        value=float(values[key])
                        if not low<=value<=high:raise ValueError('设置超出有效范围')
                        clean[key]=int(value) if key=='min_frames' else value
                path.parent.mkdir(parents=True,exist_ok=True)
                temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(clean),encoding='utf-8');temporary.replace(path)
                return clean
            try:
                values=json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(values,dict):return {}
                return {key:value for key,value in values.items() if key in ('detect','min_frames','tolerance','duplicate_mode','separate_formats')}
            except (OSError,ValueError):return {}

    def thumbnail(self,payload):
        quality=payload.get('quality','low')
        if quality not in ('low','high'):raise ValueError('预览清晰度无效')
        with self.lock:
            self._check(payload,('ready','reviewing','done','copying','paused','cancelled'))
            row=self.plan['rows'].get(str(payload.get('file')))
            if not row or row['kind'] not in ('照片','RAW原片'):raise ValueError('此文件没有照片缩略图')
            shot=self.plan['shots'][row['shot']]
            raw=next((self.plan['rows'][rid] for rid in shot['rows'] if self.plan['rows'][rid]['kind']=='RAW原片'),None)
            paired=next((self.plan['rows'][rid] for rid in shot['rows'] if self.plan['rows'][rid].get('format')=='JPEG' and self.plan['rows'][rid]['kind']=='照片'),None)
            # Validate all candidate source identities before decoding, then again on return.
            candidates=[row]
            if raw and raw not in candidates:candidates.append(raw)
            if paired and paired not in candidates:candidates.append(paired)
            version=(self.plan['id'],self.plan['revision'])
            for candidate in candidates:
                path=Path(candidate['source'])
                if path.resolve()!=path or fingerprint(path)!=candidate['fingerprint']:raise ValueError('来源已经变化，请重新扫描')
        def decode(candidate):
            path=Path(candidate['source']);is_raw=candidate['kind']=='RAW原片'
            try:
                if os.name=='nt':
                    from windows_media import thumbnail
                    result=thumbnail(path,quality=quality,raw=is_raw)
                else:
                    helper=Path(os.environ.get('LIGHTHARBOR_MEDIA_PROBE',str(BASE/'media-probe')))
                    process=subprocess.run([str(helper)],input=json.dumps({'path':str(path),'thumbnail':True,'quality':quality,'raw':is_raw})+'\n',text=True,capture_output=True,timeout=25)
                    result=json.loads(process.stdout)
                result['preview_source']=(result.get('source') or 'RAW 内嵌预览') if is_raw else ('配套 JPG' if raw and paired and candidate is paired else '照片预览')
                return result
            except Exception:return {'thumbnail':None,'warning':'此格式暂时无法预览，仍可按原文件复制','preview_source':'预览不可用'}
        first=raw if raw and quality=='high' else row
        result=decode(first)
        if paired and first is not paired and (not result.get('thumbnail') or quality=='high' and max(result.get('width',0),result.get('height',0))<2560):
            alternative=decode(paired)
            if alternative.get('thumbnail') and (not result.get('thumbnail') or max(alternative.get('width',0),alternative.get('height',0))>max(result.get('width',0),result.get('height',0))):result=alternative
        with self.lock:
            if not self.plan or version!=(self.plan['id'],self.plan['revision']):raise ValueError('预览已更新，请重试')
            for candidate in candidates:
                if fingerprint(Path(candidate['source']))!=candidate['fingerprint']:raise ValueError('来源已经变化，请重新扫描')
        if quality=='high' and result.get('thumbnail') and max(result.get('width',0),result.get('height',0))<2560:
            result.setdefault('warning','当前可用预览尺寸较小，未放大' if raw else '此照片可用预览尺寸较小')
        result['quality']=quality
        return result



class LocalServer(ThreadingHTTPServer):
    def server_bind(self):
        # Avoid DNS/reverse-lookup delays for a loopback-only service.
        socketserver.TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = self.server_address[1]


def make_handler(organizer, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def send(self, data, status=200, content='application/json; charset=utf-8'):
            body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', content)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")
            self.end_headers()
            try: self.wfile.write(body)
            except BrokenPipeError: pass

        def authorized(self):
            host = f'127.0.0.1:{self.server.server_port}'
            return self.headers.get('Host') == host and self.headers.get('Origin', f'http://{host}') == f'http://{host}' and secrets.compare_digest(self.headers.get('X-Organizer-Token', ''), token)

        def do_GET(self):
            if self.path == '/': return self.send((BASE / 'index.html').read_text().replace('@@VERSION@@', DISPLAY_VERSION).encode(), content='text/html; charset=utf-8')
            if not self.authorized(): return self.send({'error': '请使用启动时打开的页面'}, 403)
            if self.path == '/api/state': return self.send(organizer.snapshot())
            return self.send({'error': '未找到'}, 404)

        def do_POST(self):
            if not self.authorized(): return self.send({'error': '请求未授权'}, 403)
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 <= length <= 2000000: raise ValueError('请求过大')
                payload = json.loads(self.rfile.read(length) or b'{}')
                if self.path == '/api/scan': organizer.start_scan(payload)
                elif self.path == '/api/cleanup_scan': organizer.cleanup_scan(payload)
                elif self.path == '/api/cleanup_dates': return self.send(organizer.cleanup_dates(payload))
                elif self.path == '/api/cleanup_check': organizer.cleanup_check(payload)
                elif self.path == '/api/cleanup_page': return self.send(organizer.cleanup_page(payload))
                elif self.path == '/api/cleanup_execute': organizer.cleanup_execute(payload)
                elif self.path == '/api/copy': organizer.start_copy(payload)
                elif self.path == '/api/prepare': organizer.prepare(payload)
                elif self.path == '/api/edit': organizer.edit(payload)
                elif self.path == '/api/pause': organizer.pause_copy(payload)
                elif self.path == '/api/continue': organizer.continue_copy(payload)
                elif self.path == '/api/next_batch': organizer.next_batch(payload)
                elif self.path == '/api/imports': return self.send(organizer.import_details(payload))
                elif self.path == '/api/history': return self.send(organizer.history_page(payload))
                elif self.path == '/api/history_detail': return self.send(organizer.history_detail(payload))
                elif self.path == '/api/folder': return self.send(organizer.reveal_folder(payload))
                elif self.path == '/api/open_folder':
                    folder = organizer.reveal_folder(payload)
                    if os.name == 'nt': os.startfile(folder['path'])
                    else: subprocess.run(['/usr/bin/open', folder['path']], check=True)
                elif self.path == '/api/retry_failed': organizer.retry_failed(payload)
                elif self.path == '/api/scope': organizer.set_scope(payload)
                elif self.path == '/api/resume': organizer.resume_task(payload)
                elif self.path == '/api/duplicates': return self.send(organizer.duplicate_page(payload))
                elif self.path == '/api/copy_preview':
                    with organizer.lock:
                        organizer._check(payload,('reviewing',))
                        offset=payload.get('offset',0)
                        if type(offset) is not int or offset<0:raise ValueError('预览位置无效')
                        rows=organizer.active_rows()
                        return self.send({'total':len(rows),'destination':str(organizer.destination),'rows':[{'source':r['source'],'target':r['target'],'duplicate_target':r.get('duplicate_target',''),'batch_duplicate':r.get('batch_duplicate',False)} for r in rows[offset:offset+100]]})
                elif self.path == '/api/results': return self.send(organizer.result_page(payload))
                elif self.path == '/api/files': return self.send(organizer.files(payload))
                elif self.path == '/api/preferences': return self.send(organizer.preferences(payload))
                elif self.path == '/api/photo_catalog': return self.send(organizer.photo_catalog(payload))
                elif self.path == '/api/thumbnail': return self.send(organizer.thumbnail(payload))
                elif self.path == '/api/cancel': organizer.cancel.set()
                elif self.path == '/api/choose':
                    multiple = payload.get('target') == 'source' and payload.get('multiple') is True
                    if os.name == 'nt':
                        chosen = organizer.choose_folder(multiple=multiple)
                        return self.send({'paths': chosen if isinstance(chosen,list) else ([chosen] if chosen else []), 'path':chosen if isinstance(chosen,str) else ''})
                    if multiple:
                        script = 'set fs to choose folder with prompt "选择照片来源，可多选" with multiple selections allowed\nset ps to {}\nrepeat with f in fs\nset end of ps to POSIX path of f\nend repeat\nset AppleScript\'s text item delimiters to ASCII character 10\nreturn ps as text'
                        result = subprocess.run(['osascript','-e',script],capture_output=True,text=True,timeout=120)
                        return self.send({'paths':result.stdout.strip().splitlines() if result.returncode==0 else []})
                    result = subprocess.run(['osascript', '-e', 'POSIX path of (choose folder with prompt "选择文件夹")'], capture_output=True, text=True, timeout=120)
                    return self.send({'path': result.stdout.strip() if result.returncode == 0 else ''})
                else: return self.send({'error': '未找到'}, 404)
                return self.send({'ok': True})
            except Exception as exc: return self.send({'error': str(exc)}, 400)
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    token = secrets.token_urlsafe(32)
    server = LocalServer(('127.0.0.1', args.port), make_handler(Organizer(data_directory()), token))
    url = f'http://127.0.0.1:{server.server_port}/#{token}'
    print('存储卡整理工具已启动，仅此电脑可访问。\n' + url, flush=True)
    print('保持此窗口开启；结束使用后按 Control+C。', flush=True)
    if not args.no_browser: webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()

if __name__ == '__main__': main()
