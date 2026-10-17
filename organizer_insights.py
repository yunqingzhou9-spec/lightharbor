"""Import overview, cooperative pause, and local completed-run history."""
import collections
import datetime as dt
import json
import os
import unicodedata
from pathlib import Path
import re
import threading
import time


class OrganizerInsights:
    def init_insights(self):
        self.pause_requested = threading.Event()
        self.copy_started = 0
        self.pause_started = None
        self.paused_seconds = 0
        self.transferred = 0
        self.current_bytes = 0
        self.processed_bytes = 0
        self.copy_finished = None

    def duplicate_mode(self):
        return self.plan.get('options',{}).get('duplicate_mode','name')

    @staticmethod
    def name_key(path):
        return unicodedata.normalize('NFD',Path(path).name).casefold()

    def verified_digest(self, path, cancelled):
        from app import digest, fingerprint
        path=Path(path)
        if cancelled(): raise InterruptedError('已停止')
        before=fingerprint(path); key=str(path)
        cached=self.hash_cache.get(key)
        if cached and cached[0]==before: return cached[1]
        result=digest(path,cancelled)
        if fingerprint(path)!=before: raise ValueError('读取时文件发生变化：'+path.name)
        if len(self.hash_cache)>20000:self.hash_cache.clear()
        self.hash_cache[key]=(before,result)
        return result

    def name_candidates(self, rows):
        from copy_workflow import safe_existing, mount_guard
        mount_guard(self.destination)
        wanted={self.name_key(row['source']) for row in rows};found={}
        if not self.destination.exists():return found
        def error(exc):raise exc
        for folder,dirs,files in os.walk(self.destination,followlinks=False,onerror=error):
            if self.cancel.is_set():raise InterruptedError('已停止')
            dirs[:]=sorted(d for d in dirs if not d.startswith('.') and not Path(folder,d).is_symlink())
            for name in files:
                key=self.name_key(name)
                if key not in wanted:continue
                target=Path(folder,name)
                if safe_existing(target,self.destination):found.setdefault(key,[]).append(str(target))
        return found

    def assess_names(self, rows):
        from app import fingerprint
        from copy_workflow import safe_existing
        index=self.name_candidates(rows)
        for i,row in enumerate(rows):
            if self.cancel.is_set():raise InterruptedError('已停止')
            with self.lock:
                self.state.update(current=f"按文件名检查 {i+1}/{len(rows)} · "+row['relative'],check_done=i+1,check_total=len(rows))
            source=Path(row['source'])
            if source.resolve()!=source or fingerprint(source)!=row['fingerprint']:
                raise ValueError('来源扫描后已变化：'+row['relative'])
            for field in ('duplicate_target','duplicate_fingerprint','duplicate_size'):row.pop(field,None)
            candidates=[str(self.destination/row['target'])]+sorted(index.get(self.name_key(source),[]))
            for name in dict.fromkeys(candidates):
                target=Path(name)
                if self.name_key(target)!=self.name_key(source) or not safe_existing(target,self.destination):continue
                fp=fingerprint(target)
                row.update(duplicate_target=str(target),duplicate_fingerprint=fp,duplicate_size=fp[2]);break
            match=row.get('duplicate_target','')
            note=('仅文件名相同，未核对内容'+('；文件大小不同' if row['duplicate_size']!=row['size'] else '')) if match else '未找到同名文件'
            row.update(import_status='backed_up' if match else 'new',import_note=note,verified_copy=match)

    def assess_imports(self):
        from app import digest, fingerprint
        from copy_workflow import safe_existing, mount_guard
        mount_guard(self.source); mount_guard(self.destination)
        if self.duplicate_mode()=='name':
            self.assess_names(list(self.plan['rows'].values()));return
        history = self.history_candidates()
        checked = {}
        rows = list(self.plan['rows'].values())
        for index, row in enumerate(rows):
            if self.cancel.is_set(): break
            with self.lock:
                self.state['current'] = f'核对导入状态 {index + 1}/{len(rows)} · ' + row['relative']
                row['import_status'] = 'check'; row['import_note'] = '尚未核对'
            try:
                source = Path(row['source'])
                if source.resolve() != source or fingerprint(source) != row['fingerprint']:
                    raise ValueError('来源扫描后已变化')
                expected = self.verified_digest(source, self.cancel.is_set)
                if fingerprint(source) != row['fingerprint']: raise ValueError('读取时来源发生变化')
                row['source_hash'] = expected
                candidates = [str(self.destination / row['target'])] + sorted(history.get((expected, row['size']), []))
                issue = ''; matched = ''
                for name in dict.fromkeys(candidates):
                    target = Path(name)
                    if not target.exists() and not target.is_symlink(): continue
                    if not safe_existing(target, self.destination):
                        issue = '目标路径需要核对'; continue
                    before = fingerprint(target)
                    if before[2] != row['size']:
                        issue = '同名文件内容不同，将使用新编号'; continue
                    key = (name, tuple(before))
                    if key not in checked: checked[key] = self.verified_digest(target, self.cancel.is_set)
                    if fingerprint(target) != before: raise ValueError('副本核对时发生变化')
                    if checked[key] == expected: matched = name; break
                    issue = '同名文件内容不同，将使用新编号'
                with self.lock:
                    row['import_status'] = 'backed_up' if matched else ('check' if issue else 'new')
                    row['import_note'] = '已核实内容一致的副本' if matched else (issue or '未找到已核实副本')
                    row['verified_copy'] = matched
            except InterruptedError: break
            except (OSError, ValueError) as exc:
                with self.lock: row.update(import_status='check', import_note=str(exc))

    def import_date(self, row):
        return self.plan['shots'].get(row.get('shot'),{}).get('date',row.get('date','日期未知'))

    def import_overview(self):
        dates = {}
        for row in self.plan['rows'].values():
            date = self.import_date(row)
            item = dates.setdefault(date, dict(date=date, new=0, backed_up=0, check=0, total=0))
            status = row.get('import_status', 'check')
            item[status] += 1; item['total'] += 1
        return sorted(dates.values(), key=lambda item: item['date'], reverse=True)

    def import_details(self, payload):
        with self.lock:
            self._check(payload, ('ready','reviewing','copying','paused','done','cancelled','error'))
            status = payload.get('status'); date = payload.get('date')
            offset = payload.get('offset', 0)
            if status not in ('new','backed_up','check') or type(offset) is not int or offset < 0:
                raise ValueError('状态或明细位置无效')
            rows = [r for r in self.plan['rows'].values() if r.get('import_status','check') == status and (not date or self.import_date(r) == date)]
            return {'total':len(rows), 'rows':[{'name':r['relative'],'date':self.import_date(r), 'note':r.get('import_note','尚未核对'), 'copy':r.get('verified_copy','')} for r in rows[offset:offset+100]]}

    def pause_copy(self, payload):
        with self.lock:
            self._check(payload, ('copying',))
            self.pause_requested.set()
            self.state['pause_pending'] = True

    def continue_copy(self, payload):
        with self.lock:
            self._check(payload, ('paused',))
            self.pause_requested.clear()

    def copy_checkpoint(self):
        if self.pause_requested.is_set() and not self.cancel.is_set():
            with self.lock:
                self.pause_started = time.monotonic()
                self.state.update(phase='paused', pause_pending=False)
            while self.pause_requested.is_set() and not self.cancel.wait(.1): pass
            with self.lock:
                self.paused_seconds += time.monotonic() - self.pause_started
                self.pause_started = None
                self.state['phase'] = 'copying'
        return self.cancel.is_set()

    def copy_progress(self, amount):
        with self.lock:
            self.transferred += amount
            self.current_bytes += amount

    def transfer_snapshot(self):
        if not self.copy_started: return None
        now = self.copy_finished or time.monotonic()
        elapsed = max(0, now - self.copy_started - self.paused_seconds - (now-self.pause_started if self.pause_started else 0))
        speed = self.transferred / elapsed if elapsed > .2 else 0
        total = self.state.get('transfer_total', 0)
        done = min(total, self.processed_bytes + self.current_bytes)
        remaining = max(0, total-done)
        return {'done_bytes':done, 'total_bytes':total, 'speed':speed, 'elapsed':elapsed,
                'eta':remaining/speed if speed > 0 else None,
                'paused':self.state['phase']=='paused'}

    @staticmethod
    def completion_summary(results):
        folders = {}
        counts = {s:0 for s in ('已复制','重复跳过','失败')}
        for result in results:
            status = result.get('status')
            if status in counts: counts[status] += 1
            if status in ('已复制','重复跳过') and result.get('target'):
                path = str(Path(result['target']).parent)
                folder = folders.setdefault(path, {'path':path,'copied':0,'skipped':0})
                folder['copied' if status=='已复制' else 'skipped'] += 1
        return {'counts':counts, 'folders':sorted(folders.values(),key=lambda r:r['path'])}

    def save_execution(self):
        from copy_workflow import atomic_json
        if not self.store or not self.plan: return
        results = self.state.get('results', [])
        if not results: return
        ident = self.state.get('execution_id')
        if not ident: return
        record = {'id':ident,'created':dt.datetime.now().isoformat(timespec='seconds'),
                  'task':self.plan['id'],'destination':str(self.destination),'source':str(self.source),
                  'dates':sorted({self.import_date(r) for r in self.active_rows()}),
                  'phase':self.state['phase'],'report':self.state.get('report',''),
                  'results':results, **self.completion_summary(results)}
        atomic_json(self.store/'history'/(ident+'.json'), record)

    def history_page(self, payload):
        offset = payload.get('offset',0); query = str(payload.get('query','')).strip()
        if type(offset) is not int or offset < 0: raise ValueError('明细位置无效')
        records = []
        if self.store:
            for path in (self.store/'history').glob('*.json'):
                if path.is_symlink(): continue
                try:
                    item = json.loads(path.read_text())
                    if not re.fullmatch('[a-f0-9]{16}',item['id']) or path.stem != item['id']: continue
                    if query and query not in ' '.join([item['created'], *item['dates']]): continue
                    records.append({k:item[k] for k in ('id','created','dates','counts','destination','phase')})
                except (OSError,ValueError,KeyError,TypeError): continue
        records.sort(key=lambda r:r['created'],reverse=True)
        return {'total':len(records),'rows':records[offset:offset+20]}

    def history_record(self, ident):
        if not self.store or not re.fullmatch('[a-f0-9]{16}',str(ident)): raise ValueError('记录不存在')
        path = self.store/'history'/(ident+'.json')
        if path.is_symlink(): raise ValueError('记录路径异常')
        return json.loads(path.read_text())

    def history_detail(self, payload):
        item = self.history_record(payload.get('history_id'))
        offset = payload.get('offset',0)
        if type(offset) is not int or offset < 0: raise ValueError('明细位置无效')
        return {**{k:item[k] for k in ('created','counts','folders','destination','report')},
                'results':item['results'][offset:offset+200],'total':len(item['results'])}

    def reveal_folder(self, payload):
        from copy_workflow import mount_guard
        with self.lock:
            if payload.get('history_id'):
                item = self.history_record(payload['history_id'])
                folders = item['folders']; root = Path(item['destination'])
            else:
                self._check(payload, ('done','cancelled','error'))
                folders = self.completion_summary(self.state.get('results',[]))['folders']; root = self.destination
            folder = next((Path(f['path']) for f in folders if f['path'] == payload.get('folder')), None)
            if folder is None: raise ValueError('请选择结果中的文件夹')
            mount_guard(root)
            if folder != root and root not in folder.parents: raise ValueError('文件夹不在此归档目录内')
            if folder.resolve() != folder or not folder.is_dir(): raise ValueError('文件夹已移动或磁盘未连接')
            return {'path':str(folder)}

    def retry_failed(self, payload):
        with self.lock:
            self._check(payload, ('done','cancelled','error'))
            failed = {r['source'] for r in self.state.get('results',[]) if r.get('status')=='失败'}
            shots = [sid for sid,s in self.plan['shots'].items() if any(self.plan['rows'][rid]['source'] in failed for rid in s['rows'])]
            if not shots: raise ValueError('没有失败照片需要重试')
            self.state['phase']='ready'
            self.set_scope(dict(payload,mode='photos',shots=shots))
        # Recheck first; successful associated files still need explicit skip consent.
        self.prepare({'plan_id':self.plan['id'],'revision':self.plan['revision']})
