#!/usr/bin/env python3
"""Local, copy-only media organizer. Python standard library + macOS helper."""
import argparse
import copy
import datetime as dt
import hashlib
import json
import math
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
from copy_workflow import CopyWorkflow, data_directory
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = Path(__file__).resolve().parent
IMAGES = set('jpg jpeg heic heif png tif tiff webp gif bmp avif'.split())
RAW = set('cr2 cr3 nef nrw arw raf dng orf rw2 pef srw'.split())
VIDEOS = set('mp4 mov m4v avi mkv mts m2ts 3gp webm'.split())
SCENES = {
    '海滩与海洋': {'beach', 'coast', 'ocean', 'sea', 'seashore', 'seaside', 'surf', 'shore'},
    '山景': {'mountain', 'mountains', 'mountain_peak', 'cliff', 'canyon'},
    '森林与植物': {'forest', 'woods', 'jungle', 'woodland', 'tree', 'trees', 'flower', 'flowers', 'plant'},
    '湖泊与河流': {'lake', 'river', 'waterfall', 'stream', 'pond'},
    '雪景': {'snow', 'snowfield', 'glacier', 'ice', 'ski'},
    '城市与建筑': {'city', 'cityscape', 'skyscraper', 'building', 'buildings', 'architecture', 'street', 'bridge'},
    '日出与日落': {'sunset', 'sunrise'},
    '动物': {'animal', 'bird', 'dog', 'cat', 'wildlife', 'horse'},
    '食物': {'food', 'dish', 'meal', 'dessert', 'fruit'},
    '人物': {'person', 'people', 'portrait', 'human', 'selfie'},
}


def kind(path):
    ext = path.suffix.lower().lstrip('.')
    if ext in IMAGES: return '照片'
    if ext in RAW: return 'RAW原片'
    if ext in VIDEOS: return '视频'
    if ext in {'wav', 'mp3', 'aac', 'flac', 'm4a'}: return '音频'
    if ext in {'xmp', 'aae', 'thm', 'lrv'}: return '伴随文件'
    return '其他文件'


def scene_label(labels):
    scores = {}
    for item in labels:
        label = str(item.get('label', '')).lower().replace(' ', '_').replace('-', '_')
        score = float(item.get('score', 0))
        for category, words in SCENES.items():
            if label in words: scores[category] = max(scores.get(category, 0), score)
    ranked = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
    if not ranked or ranked[0][1] < .35: return '景色待确认', 0
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < .08: return '景色待确认', ranked[0][1]
    return ranked[0]


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


class Probe:
    def __init__(self):
        self.proc = None
        helper = BASE / 'media-probe'
        if os.name != 'nt' and helper.is_file():
            try:
                self.proc = subprocess.Popen([str(helper)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
            except OSError: pass

    def read(self, path, scene):
        if os.name == 'nt':
            from windows_media import metadata
            return metadata(path, scene)
        if not self.proc: return {'warning': '图片读取组件不可用；使用修改日期，地点和景色标记为未知'}
        try:
            self.proc.stdin.write(json.dumps({'path': str(path), 'scene': scene}) + '\n')
            self.proc.stdin.flush()
            if not select.select([self.proc.stdout], [], [], 45)[0]:
                self.close()
                return {'warning': '图片识别超时；后续图片使用基础分类'}
            return json.loads(self.proc.stdout.readline())
        except (OSError, ValueError):
            self.close()
            return {'warning': '图片读取组件异常；使用基础分类'}

    def close(self):
        if self.proc:
            self.proc.kill()
            self.proc.wait()
            self.proc.stdin.close()
            self.proc.stdout.close()
            self.proc = None


def scan(source, destination, options, progress=lambda *_: None, cancelled=lambda: False):
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
                    meta = probe.read(p, bool(options.get('scene'))) if media_kind in ('照片', 'RAW原片') else {}
                    date, date_source = dt.datetime.fromtimestamp(fp[3] / 1e9), '文件修改日期'
                    if meta.get('date'):
                        try:
                            date = dt.datetime.strptime(meta['date'][:19], '%Y:%m:%d %H:%M:%S')
                            date_source = '拍摄日期'
                        except ValueError: pass
                    reliable = date_source == '拍摄日期' and bool(meta.get('reliable_time', True))
                    if reliable and str(meta.get('subsecond', '')).isdigit():
                        date = date.replace(microsecond=int((str(meta['subsecond']) + '000000')[:6]))
                    place = '位置未知'
                    gps = meta.get('gps')
                    if gps and len(gps) == 2 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in gps) and abs(gps[0]) <= 90 and abs(gps[1]) <= 180:
                        # 0.02 degree fixed grid, deterministic across runs; not city names.
                        lat, lon = (math.floor(v / .02) * .02 for v in gps)
                        place = f'GPS_{lat:+.2f}_{lon:+.2f}'
                    scene, confidence = scene_label(meta.get('labels', [])) if options.get('scene') else ('未启用', 0)
                    if media_kind not in ('照片', 'RAW原片'): scene = '不适用'
                    parts = []
                    if options.get('date', True): parts += [date.strftime('%Y'), date.strftime('%Y-%m-%d')]
                    if options.get('type', True): parts += [media_kind]
                    if options.get('location'): parts += [place]
                    if options.get('scene'): parts += [scene]
                    rows.append({'source': str(p), 'relative': p.relative_to(source).as_posix(), 'target': Path(*parts, name).as_posix(), 'size': fp[2], 'fingerprint': fp, 'date_source': date_source, 'date':date.strftime('%Y-%m-%d'), 'capture_time':date.isoformat(), 'reliable_time':reliable, 'camera':meta.get('camera',''), 'kind':media_kind, 'location': place, 'scene': scene, 'confidence': round(confidence, 3), 'warning': meta.get('warning', '')})
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

    def _check(self, payload, phases=('ready','reviewing')):
        if self.state['phase'] not in phases or not self.plan: raise ValueError('当前不能调整或确认，请等待扫描完成')
        if payload.get('plan_id') != self.plan['id'] or payload.get('revision') != self.plan['revision']:
            raise ValueError('预览已更新，请刷新后重试；未执行操作')

    def start_scan(self, payload):
        with self.lock:
            if self.state['phase'] in ('scanning','preparing','copying','paused'): raise ValueError('请等待当前任务完成')
            if not payload.get('source') or not payload.get('destination'):raise ValueError('请填写来源和目标路径')
            source,dest=valid_roots(payload['source'],payload['destination'])
            options=payload.get('options',{})
            if options.get('duplicate_mode','name') not in ('name','content'):raise ValueError('查重方式无效')
            minimum=int(options.get('min_frames',30));tolerance=float(options.get('tolerance',20))
            if not 3<=minimum<=100000 or not 5<=tolerance<=60:raise ValueError('连续张数 3–100000，间隔容差 5–60%')
            self.source,self.destination,self.rows=source,dest,[]
            self.plan=None;self.history=[];self.cancel.clear();self.init_insights()
            self.state={'phase':'scanning','count':0,'current':'','warnings':[],'results':[],'version':2,'source':str(source),'destination':str(dest),'options':options}
        threading.Thread(target=self._scan,args=(options,),daemon=True).start()

    def _scan(self,options):
        try:
            def progress(n,name):
                with self.lock:self.state.update(count=n,current=name)
            rows,warnings=scan(self.source,self.destination,options,progress,self.cancel.is_set)
            plan=planner.build_plan(rows,options)
            with self.lock:
                self.rows=rows;self.plan=plan;self.revision=1
            self.assess_imports()
            with self.lock:
                self.state.update(phase='cancelled' if self.cancel.is_set() else 'ready',warnings=warnings,metadata_warnings=sum(bool(r['warning']) for r in rows),fallback_dates=sum(not r['reliable_time'] for r in rows))
                self._publish(); self.save_task()
        except Exception as exc:
            with self.lock:self.state.update(phase='error',error=str(exc))

    def edit(self,payload):
        with self.lock:
            self._check(payload)
            if payload.get('action')=='undo':
                if not self.history:raise ValueError('没有可撤销的调整')
                checkpoint=self.history.pop()
                self.plan['groups']=checkpoint['groups']
                for sid,excluded in checkpoint['excluded'].items():self.plan['shots'][sid]['excluded']=excluded
            else:
                if payload.get('all_group'):
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
            return planner.page(self.plan,payload.get('group'),int(payload.get('page',0)))

    def thumbnail(self,payload):
        with self.lock:
            self._check(payload,('ready','reviewing','done','copying','paused','cancelled'))
            row=self.plan['rows'].get(str(payload.get('file')))
            if not row or row['kind'] not in ('照片','RAW原片'):raise ValueError('此文件没有照片缩略图')
            path=Path(row['source'])
            if path.resolve()!=path or fingerprint(path)!=row['fingerprint']:raise ValueError('来源已经变化，请重新扫描')
        try:
            if os.name == 'nt':
                from windows_media import thumbnail
                return thumbnail(path)
            result=subprocess.run([str(BASE/'media-probe')],input=json.dumps({'path':str(path),'thumbnail':True})+'\n',text=True,capture_output=True,timeout=20)
            data=json.loads(result.stdout)
            return {'thumbnail':data.get('thumbnail'),'warning':data.get('warning','无法读取缩略图')}
        except Exception:return {'thumbnail':None,'warning':'此格式暂时无法预览，仍可按原文件复制'}



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
            if self.path == '/': return self.send((BASE / 'index.html').read_bytes(), content='text/html; charset=utf-8')
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
                elif self.path == '/api/copy': organizer.start_copy(payload)
                elif self.path == '/api/prepare': organizer.prepare(payload)
                elif self.path == '/api/edit': organizer.edit(payload)
                elif self.path == '/api/pause': organizer.pause_copy(payload)
                elif self.path == '/api/continue': organizer.continue_copy(payload)
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
                elif self.path == '/api/results': return self.send(organizer.result_page(payload))
                elif self.path == '/api/files': return self.send(organizer.files(payload))
                elif self.path == '/api/thumbnail': return self.send(organizer.thumbnail(payload))
                elif self.path == '/api/cancel': organizer.cancel.set()
                elif self.path == '/api/choose':
                    if os.name == 'nt':
                        return self.send({'path': organizer.choose_folder()})
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
