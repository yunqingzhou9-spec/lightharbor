"""Persistent copy plans, explicit copy scope and user-confirmed duplicate handling."""
import copy
import datetime as dt
import json
import os
import re
from pathlib import Path
import secrets
import shutil
import threading
import time
from organizer_insights import OrganizerInsights
from card_cleanup import CardCleanup
import unicodedata


def data_directory():
    default = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'LightHarbor/tasks' if os.name == 'nt' else Path.home() / 'Library/Application Support/LightHarbor/tasks'
    return Path(os.environ.get('LIGHTHARBOR_DATA_DIR', str(default)))


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name('.' + path.name + '-' + secrets.token_hex(4))
    try:
        with temporary.open('x', encoding='utf-8') as out:
            os.chmod(temporary, 0o600)
            json.dump(value, out, ensure_ascii=False)
            out.flush(); os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def safe_existing(path, root):
    """History entries never authorize reads outside the chosen destination."""
    path, root = Path(path), Path(root)
    return (path.is_absolute() and root in path.parents and path.resolve() == path
            and not path.is_symlink() and path.is_file())


def mount_guard(path):
    path = Path(path)
    if os.name == 'nt' and path.anchor and not Path(path.anchor).exists():
        raise ValueError('磁盘未连接：' + path.anchor)
    if len(path.parts) >= 3 and path.parts[1] == 'Volumes':
        volume = Path(*path.parts[:3])
        if not volume.is_mount():
            raise ValueError('外置磁盘未连接：' + str(volume) + '。请接回原磁盘后重试。')


class CopyWorkflow(CardCleanup, OrganizerInsights):
    def init_workflow(self, store):
        self.init_insights()
        self.hash_cache={}
        self.store = Path(store) if store else None
        self.saved_tasks = []
        self._load_task_list()

    def _load_task_list(self):
        self.saved_tasks = []
        if not self.store or not self.store.exists(): return
        for path in self.store.glob('*.json'):
            try:
                if path.is_symlink(): continue
                value = json.loads(path.read_text())
                task_id = value['plan']['id']
                if not re.fullmatch(r'[a-f0-9]{16}', task_id) or path.stem != task_id: continue
                self.saved_tasks.append({'id': value['plan']['id'], 'updated': value['updated'],
                    'source': value['source'], 'destination': value['destination'],
                    'phase': value['phase'], 'files': value['files']})
            except (OSError, ValueError, KeyError, TypeError): continue
        self.saved_tasks.sort(key=lambda x: x['updated'], reverse=True)

    def save_task(self):
        if not self.store or not self.plan: return
        value = {'schema': 1, 'plan': self.plan, 'source': str(self.source),
            'destination': str(self.destination), 'phase': self.state['phase'],
            'files': len(self.active_rows()), 'updated': dt.datetime.now().isoformat(timespec='seconds')}
        atomic_json(self.store / (self.plan['id'] + '.json'), value)
        self._load_task_list()

    def active_shots(self):
        scope = self.plan.get('scope', {'mode': 'all'})
        chosen = set(scope.get('shots', []))
        return {sid for sid, shot in self.plan['shots'].items()
                if not shot['excluded'] and (scope['mode'] == 'all' or sid in chosen)}

    def active_rows(self):
        shots = self.active_shots()
        return [self.plan['rows'][rid] for sid, s in self.plan['shots'].items()
                if sid in shots for rid in s['rows']]

    def set_scope(self, payload):
        import planner
        with self.lock:
            self._check(payload, phases=('ready', 'reviewing', 'done'))
            mode = payload.get('mode')
            if mode == 'all': scope = {'mode': 'all', 'shots': []}
            elif mode == 'toggle_shots':
                matches=set(payload.get('shots',[]))
                if payload.get('all_group'):
                    matches.update(next((g['shots'] for g in self.plan['groups'] if g['id']==payload.get('group')),[]))
                if not matches or not matches.issubset(self.plan['shots']) or not isinstance(payload.get('checked'),bool):
                    raise ValueError('照片选择无效')
                shots=self.active_shots()
                if payload['checked']: shots.update(matches)
                else: shots.difference_update(matches)
                scope={'mode':'photos','shots':sorted(shots)}
            elif mode == 'toggle_folder':
                folder = payload.get('folder')
                if not isinstance(folder,str) or folder.startswith('/') or '..' in Path(folder).parts or not isinstance(payload.get('checked'),bool):
                    raise ValueError('文件夹选择无效')
                matches = {sid for g in self.plan['groups'] if folder == '' or g['folder'] == folder or g['folder'].startswith(folder+'/') for sid in g['shots']}
                if not matches: raise ValueError('文件夹不存在')
                shots = self.active_shots()
                if payload['checked']: shots.update(matches)
                else: shots.difference_update(matches)
                scope = {'mode':'folders','shots':sorted(shots)}
            elif mode in ('folders', 'photos'):
                if mode == 'folders':
                    folders = payload.get('folders', [])
                    if not folders or any(not isinstance(p, str) or p.startswith('/') or '..' in Path(p).parts for p in folders):
                        raise ValueError('请选择有效的预览文件夹')
                    shots = {sid for g in self.plan['groups']
                             if any(p == '' or g['folder'] == p or g['folder'].startswith(p + '/') for p in folders)
                             for sid in g['shots']}
                    scope = {'mode': mode, 'shots': sorted(shots), 'folders': folders}
                else:
                    shots = set(payload.get('shots', []))
                    if payload.get('all_group'):
                        shots.update(next((g['shots'] for g in self.plan['groups'] if g['id'] == payload.get('group')), []))
                    if payload.get('append') and self.plan.get('scope', {}).get('mode') == 'photos':
                        shots.update(self.plan['scope']['shots'])
                    if not shots or not shots.issubset(self.plan['shots']): raise ValueError('请选择有效的照片')
                    scope = {'mode': mode, 'shots': sorted(shots)}
                if not any(not self.plan['shots'][sid]['excluded'] for sid in shots):
                    raise ValueError('所选内容均已排除，请先恢复整理')
            else: raise ValueError('复制范围无效')
            if mode in ('all','toggle_shots','toggle_folder'):
                for shot in self.plan['shots'].values(): shot['excluded']=False
            self.plan['scope'] = scope
            self.revision += 1; self.plan['revision'] = self.revision
            planner.refresh(self.plan)
            self.state.update(phase='ready', preflight=None, results=[], error='')
            self._publish(); self.save_task()

    def resume_task(self, payload):
        import planner
        from app import valid_roots, fingerprint, digest
        with self.lock:
            if self.state['phase'] in ('scanning', 'preparing', 'copying', 'paused'): raise ValueError('请先等待或停止当前操作')
            task = next((t for t in self.saved_tasks if t['id'] == payload.get('task')), None)
            if not task or not self.store: raise ValueError('保存的任务不存在')
            path = self.store / (task['id'] + '.json')
            if path.is_symlink(): raise ValueError('任务记录路径异常')
            saved = json.loads(path.read_text())
            mount_guard(saved['source']); mount_guard(saved['destination'])
            src, dest = valid_roots(saved['source'], saved['destination'])
            if str(src) != saved['source'] or str(dest) != saved['destination']: raise ValueError('任务路径已改变，请重新扫描')
            plan = saved['plan']
            # Metadata must still match; changed device/inode on remount is allowed only
            # when the previous content hash proves that it is still the same file.
            self.cancel.clear()
            self.state.update(phase='preparing', current='恢复保存的整理计划', error='')
        def restore():
            try:
                for row in plan['rows'].values():
                    if self.cancel.is_set(): raise InterruptedError('已停止恢复')
                    p = Path(row['source'])
                    if p.resolve() != p or src not in p.parents: raise ValueError('来源路径已经改变')
                    current = fingerprint(p)
                    if current != row['fingerprint']:
                        if not row.get('source_hash') or digest(p, self.cancel.is_set) != row['source_hash'] or fingerprint(p) != current:
                            raise ValueError('来源已经变化，请重新扫描：' + row['relative'])
                        row['fingerprint'] = current
                if any(shot['excluded'] for shot in plan['shots'].values()):
                    old=plan.get('scope',{'mode':'all'})
                    included=[sid for sid,shot in plan['shots'].items() if not shot['excluded'] and (old['mode']=='all' or sid in old.get('shots',[]))]
                    plan['scope']={'mode':'photos','shots':included}
                    for shot in plan['shots'].values(): shot['excluded']=False
                planner.refresh(plan)
                with self.lock:
                    self.plan = plan; self.rows = list(plan['rows'].values())
                    self.source, self.destination = src, dest
                    self.revision = plan['revision'] + 1; plan['revision'] = self.revision
                    self.history = []
                    self.init_insights()
                    self.state = {'phase': 'ready', 'results': [], 'warnings': [], 'current': '',
                                  'version': 3, 'resume_notice': '已恢复调整和复制范围。请检查并确认，已有副本需要你选择是否跳过。'}
                    self._publish(); self.save_task()
            except Exception as exc:
                with self.lock: self.state.update(phase='ready' if self.plan else 'idle', current='', error=str(exc))
        threading.Thread(target=restore, daemon=True).start()

    def prepare(self, payload):
        import planner
        with self.lock:
            self._check(payload)
            if not self.active_rows(): raise ValueError('没有选中要复制的文件')
            planner.refresh(self.plan)
            self.cancel.clear(); self.state.update(phase='preparing', current='核对来源与已有副本', error='', preflight=None)
        threading.Thread(target=self._prepare, daemon=True).start()

    def history_candidates(self):
        """Read only this archive's own logs, never crawl unrelated photo folders."""
        records = self.destination / '.整理记录'
        found = {}
        if records.is_symlink() or records.resolve() != records or not records.is_dir(): return found
        for log in records.glob('*.jsonl'):
            if self.cancel.is_set(): raise InterruptedError('已停止检查')
            if not safe_existing(log, self.destination): continue
            try:
                with log.open(encoding='utf-8') as stream:
                    for line in stream:
                        if self.cancel.is_set(): raise InterruptedError('已停止检查')
                        try:
                            r = json.loads(line)
                            if r.get('status') not in ('已复制', '重复跳过'): continue
                            target = Path(r['target'])
                            if safe_existing(target, self.destination):
                                found.setdefault((r['sha256'], target.stat().st_size), set()).add(str(target))
                        except (ValueError, KeyError, TypeError, OSError): continue
            except (OSError, UnicodeError): continue
        return found

    def _prepare(self):
        import planner
        from app import valid_roots, fingerprint, digest
        try:
            active = self.active_rows()
            mount_guard(self.source); mount_guard(self.destination)
            src, dest = valid_roots(self.source, self.destination)
            if src != self.source or dest != self.destination: raise ValueError('来源或目标路径已经改变')
            reserved={};renamed=0
            if self.duplicate_mode()=='name':
                self.assess_names(active)
                duplicates=sum(bool(row.get('duplicate_target')) for row in active)
                reserved=set()
                def key(p):return unicodedata.normalize('NFD',str(p)).casefold()
            else:
                history = self.history_candidates()
                checked = {}; reserved = set(); duplicates = 0; renamed = 0
                def key(p): return unicodedata.normalize('NFD', str(p)).casefold()
                def same_file(p, row):
                    if not safe_existing(p, dest): return False
                    fp = fingerprint(p)
                    if fp[2] != row['size']: return False
                    cache = (str(p), tuple(fp))
                    if cache not in checked: checked[cache] = self.verified_digest(p, self.cancel.is_set)
                    return checked[cache] == row['source_hash'] and fingerprint(p) == fp
                for row in active:
                    if self.cancel.is_set(): raise InterruptedError('已停止检查')
                    p = Path(row['source'])
                    if p.resolve() != p or fingerprint(p) != row['fingerprint']: raise ValueError('来源扫描后已变化：' + row['relative'])
                    with self.lock: self.state['current'] = '核对内容 · ' + row['relative']
                    row['source_hash'] = self.verified_digest(p, self.cancel.is_set)
                    if fingerprint(p) != row['fingerprint']: raise ValueError('读取时来源发生变化：' + row['relative'])
                    row.pop('duplicate_target', None)
                    candidate = dest / row['target']
                    candidates = [str(candidate)] + sorted(history.get((row['source_hash'], row['size']), []))
                    for name in dict.fromkeys(candidates):
                        target = Path(name)
                        if same_file(target, row):
                            row['duplicate_target'] = str(target); duplicates += 1; break
                    row.update(import_status='backed_up' if row.get('duplicate_target') else ('check' if row.get('import_status')=='check' else 'new'), verified_copy=row.get('duplicate_target',''))
            selected = self.active_shots()
            for group in self.plan['groups']:
                for sid in group['shots']:
                    if sid not in selected: continue
                    rows = [self.plan['rows'][rid] for rid in self.plan['shots'][sid]['rows']]
                    suffix = 0
                    while True:
                        proposals = []; conflict = False
                        for row in rows:
                            rel = Path(row['target'])
                            if rel.is_absolute() or '..' in rel.parts: raise ValueError('目标路径无效')
                            if suffix:
                                stem = rel.stem
                                if rel.suffix.lower() in ('.xmp', '.aae') and Path(stem).suffix:
                                    stem = Path(stem).stem + f'__{suffix}' + Path(stem).suffix
                                else: stem += f'__{suffix}'
                                rel = rel.with_name(stem + rel.suffix)
                            target = dest / rel
                            for parent in [target.parent, *target.parent.parents]:
                                if parent == dest.parent: break
                                if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                                    raise ValueError('目标目录不是安全的普通目录：' + str(parent))
                            # Reserve a new name even for duplicates; choosing "copy again"
                            # can never silently turn into a skip or overwrite.
                            if key(target) in reserved or os.path.lexists(target): conflict = True; break
                            proposals.append((row, rel.as_posix()))
                        if not conflict: break
                        suffix += 1
                        if self.cancel.is_set(): raise InterruptedError('已停止检查')
                    for row, rel in proposals:
                        row['target'] = rel; reserved.add(key(dest / rel)); renamed += int(bool(suffix))
            with self.lock:
                self.revision += 1; self.plan['revision'] = self.revision
                self.state.update(phase='reviewing', preflight={'duplicate_mode':self.duplicate_mode(), 'renamed': renamed, 'duplicates': duplicates,
                    'files': len(active), 'bytes': sum(r['size'] for r in active),
                    'new_bytes': sum(r['size'] for r in active if not r.get('duplicate_target'))}, current='')
                self._publish(); self.save_task()
        except Exception as exc:
            with self.lock:
                planner.refresh(self.plan)
                self.state.update(phase='ready', preflight=None, error=str(exc))

    def duplicate_page(self, payload):
        with self.lock:
            self._check(payload, ('reviewing',))
            offset = payload.get('offset', 0)
            if type(offset) is not int or offset < 0: raise ValueError('明细位置无效')
            rows = [r for r in self.active_rows() if r.get('duplicate_target')]
            return {'total': len(rows), 'rows': [{'source': r['source'], 'existing': r['duplicate_target'],
                'copy_target': str(self.destination / r['target']), 'size': r['size'], 'existing_size':r.get('duplicate_size'), 'match_mode':self.duplicate_mode()} for r in rows[offset:offset+100]]}

    def start_copy(self, payload=None):
        from app import valid_roots, fingerprint
        with self.lock:
            payload = payload or {}
            self._check(payload, ('reviewing',))
            policy = payload.get('duplicate_policy')
            if self.state['preflight']['duplicates'] and policy not in ('skip', 'again'):
                raise ValueError('发现已有副本，请手动选择“跳过匹配文件”或“全部再复制一份”')
            mount_guard(self.source); mount_guard(self.destination)
            source, dest = valid_roots(self.source, self.destination)
            if source != self.source or dest != self.destination: raise ValueError('路径已变化，请重新检查')
            for r in self.active_rows():
                if Path(r['source']).resolve() != Path(r['source']) or fingerprint(Path(r['source'])) != r['fingerprint']:
                    raise ValueError('来源在检查后发生变化，请重新扫描')
            ancestor = dest
            while not ancestor.exists(): ancestor = ancestor.parent
            needed = sum(r['size'] for r in self.active_rows() if policy != 'skip' or not r.get('duplicate_target'))
            if shutil.disk_usage(ancestor).free < needed: raise ValueError('目标剩余空间不足，请缩小复制范围')
            self.init_insights(); self.copy_started=time.monotonic()
            self.cancel.clear(); self.state.update(transfer_total=needed, execution_id=secrets.token_hex(8), pause_pending=False, phase='copying', completed=0, results=[], error='', duplicate_policy=policy or 'again')
            try: self.save_task()
            except OSError:
                self.state['phase'] = 'reviewing'
                raise ValueError('无法保存续复制任务，尚未开始复制。请检查本机剩余空间和权限。')
        threading.Thread(target=self._copy, daemon=True).start()

    def _copy(self):
        from app import safe_folder, copy_one, fingerprint, digest
        report_path = None
        try:
            mount_guard(self.source); mount_guard(self.destination)
            self.destination.mkdir(parents=True, exist_ok=True)
            if self.destination.resolve() != self.destination: raise ValueError('目标路径已改变')
            reports = safe_folder(self.destination, Path('.整理记录'))
            report_path = reports / f'{dt.datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(4)}.jsonl'
            selected = self.active_shots()
            with report_path.open('x', encoding='utf-8') as log:
                for group in self.plan['groups']:
                    for sid in group['shots']:
                        if self.cancel.is_set(): break
                        if sid not in selected: continue
                        for rid in self.plan['shots'][sid]['rows']:
                            if self.copy_checkpoint(): break
                            row = self.plan['rows'][rid]
                            with self.lock:
                                self.current_bytes=0; self.state['current']=row['relative']
                            try:
                                mount_guard(self.source); mount_guard(self.destination)
                                if self.state['duplicate_policy'] == 'skip' and row.get('duplicate_target'):
                                    source = Path(row['source']); target = Path(row['duplicate_target'])
                                    if source.resolve() != source or fingerprint(source) != row['fingerprint']:
                                        raise ValueError('来源在检查后发生变化')
                                    if not safe_existing(target, self.destination): raise ValueError('已有副本已移走，请重新检查')
                                    before = fingerprint(target)
                                    if self.duplicate_mode()=='name':
                                        if before!=row.get('duplicate_fingerprint') or self.name_key(target)!=self.name_key(source):
                                            raise ValueError('同名文件在确认后已变化，请重新检查')
                                        result={'status':'重复跳过','target':str(target),'match_mode':'name','content_verified':False}
                                    else:
                                        if self.verified_digest(source, self.copy_checkpoint) != row['source_hash'] or self.verified_digest(target, self.copy_checkpoint) != row['source_hash'] or fingerprint(target) != before or fingerprint(source) != row['fingerprint']:
                                            raise ValueError('已有副本或来源已变化，未跳过，请重新检查')
                                        result = {'status': '重复跳过', 'target': str(target), 'sha256': row['source_hash'], 'match_mode':'content','content_verified':True}

                                else:
                                    result = copy_one(row, self.source, self.destination, self.copy_checkpoint, exact=True, allow_duplicate=False, progress=self.copy_progress)
                            except InterruptedError: break
                            except Exception as exc: result = {'status': '失败', 'error': str(exc)}
                            result.update(file_id=rid, date=self.import_date(row), source=row['source'], planned_target=row['target'], group=group['name'])
                            log.write(json.dumps(result, ensure_ascii=False) + '\n'); log.flush(); os.fsync(log.fileno())
                            with self.lock:
                                self.processed_bytes += row['size'] if not (self.state['duplicate_policy']=='skip' and row.get('duplicate_target')) else 0
                                self.current_bytes=0
                                if result['status'] in ('已复制','重复跳过'):
                                    row.update(import_status='backed_up',import_note='按文件名确认跳过，未核对内容' if result.get('match_mode')=='name' else '已核实内容一致的副本',verified_copy=result['target'])
                                else:
                                    row.update(import_status='check',import_note=result.get('error','需要重新核对'))
                                self.state['results'].append(result); self.state['completed'] += 1; self.state['current'] = row['relative']
            with self.lock:
                self.copy_finished=time.monotonic(); self.current_bytes=0
                self.state.update(phase='cancelled' if self.cancel.is_set() else 'done', report=str(report_path))
                self.save_task(); self.save_execution()
        except Exception as exc:
            with self.lock:
                self.copy_finished=time.monotonic()
                self.state.update(phase='error', error=str(exc), report=str(report_path) if report_path else '')
                try: self.save_task(); self.save_execution()
                except OSError: pass
