"""Explicit, dated source-card deletion after verified off-volume backup.
No source mutation during preview. A one-use, expiring confirmation is required.
"""
import datetime as dt
import json
import os
from pathlib import Path
import secrets
import threading
import time


def source_card_guard(source):
    from copy_workflow import mount_guard
    if not source or not str(source).strip():raise ValueError('请选择要清理的照片文件夹')
    source=Path(source).expanduser().resolve()
    mount_guard(source)
    if not source.is_dir():raise ValueError('照片文件夹不存在或无法访问，请重新选择')
    return source


def card_guard(source, destination):
    from copy_workflow import mount_guard
    from app import valid_roots
    source,destination=valid_roots(source,destination)
    source_card_guard(source);mount_guard(destination)
    if not destination.is_dir():raise ValueError('请先连接并选择已有备份的目标文件夹')
    if source.stat().st_dev==destination.stat().st_dev:
        raise ValueError('备份必须位于另一个磁盘卷，不能使用来源磁盘自身的副本')
    return source,destination


def off_volume(source,target):
    return source.stat().st_dev != target.stat().st_dev


class CardCleanup:
    def cleanup_scan(self,payload):
        source=source_card_guard(payload.get('source',''))
        if not self.store:raise ValueError('无法保存清理日志')
        # An internal non-existent planning root only; copy endpoints reject this plan.
        self.start_scan({'source':str(source),'destination':str(self.store.parent/'cleanup-plan-only'),
                         'options':{'cleanup_only':True,'detect':True,'scene':False,'location':False}})

    def cleanup_dates(self, payload):
        with self.lock:
            self._check(payload,('ready','reviewing','done'))
            dates={}
            for shot in self.plan['shots'].values():
                if not shot['photo']:continue
                entry=dates.setdefault(shot['date'],{'date':shot['date'],'files':0,'bytes':0,'unreliable':0})
                for rid in shot['rows']:
                    row=self.plan['rows'][rid];entry['files']+=1;entry['bytes']+=row['size']
                entry['unreliable']+=int(not shot['reliable'])
            shots=[]
            for shot in self.plan['shots'].values():
                if not shot['photo']:continue
                rows=[self.plan['rows'][i] for i in shot['rows']]
                preview=next((i for i in shot['rows'] if self.plan['rows'][i].get('format')=='JPEG'),next((i for i in shot['rows'] if self.plan['rows'][i]['kind'] in ('照片','RAW原片')),None))
                shots.append({'id':shot['id'],'date':shot['date'],'preview':preview,'name':rows[0]['relative'],'count':len(rows),'bytes':sum(r['size'] for r in rows),'files':[r['relative'] for r in rows],'kind':next((g['kind'] for g in self.plan['groups'] if shot['id'] in g['shots']),'ordinary')})
            return {'source':str(self.source),'destination':str(self.destination),'dates':sorted(dates.values(),key=lambda x:x['date']),'shots':shots}

    def cleanup_check(self,payload):
        with self.lock:
            self._check(payload,('ready','reviewing','done'))
            if not self.store:raise ValueError('无法保存清理日志，不能清理照片')
            mode=payload.get('mode','verified')
            if mode not in ('direct','verified'):raise ValueError('清理方式无效')
            if mode=='direct':source_card_guard(self.source)
            else:card_guard(self.source,self.destination)
            dates=payload.get('dates')
            available={s['date'] for s in self.plan['shots'].values() if s['photo']}
            if not isinstance(dates,list) or not dates or any(not isinstance(d,str) or d not in available for d in dates):raise ValueError('请选择有效的拍摄日期')
            selected=payload.get('shots')
            if selected is not None:
                valid={s['id'] for s in self.plan['shots'].values() if s['photo'] and s['date'] in dates}
                if not isinstance(selected,list) or not selected or any(not isinstance(i,str) or i not in valid for i in selected):raise ValueError('所选照片无效，请重新选择')
                selected=list(dict.fromkeys(selected))
            self.cleanup={'shots':selected,'phase':'checking','mode':mode,'revision':self.revision,'plan_id':self.plan['id'],'dates':sorted(set(dates)),'rows':[],'error':''}
            self.cancel.clear();self.state.update(phase='preparing',preflight=None,current='正在生成待删除清单' if mode=='direct' else '正在完整核对原片与备份',error='')
        threading.Thread(target=self._cleanup_check,daemon=True).start()

    def _cleanup_check(self):
        from app import digest,fingerprint
        from copy_workflow import safe_existing
        try:
            shots=[s for s in self.plan['shots'].values() if s['photo'] and s['date'] in self.cleanup['dates'] and (self.cleanup.get('shots') is None or s['id'] in self.cleanup['shots'])]
            rows=[self.plan['rows'][rid] for s in shots for rid in s['rows']]
            direct=self.cleanup.get('mode')=='direct'
            names={} if direct else self.name_candidates(rows);history={} if direct else self.history_candidates();result=[]
            for shot in shots:
                batch=[]
                for rid in shot['rows']:
                    if self.cancel.is_set():raise InterruptedError('已停止核对，没有删除原片')
                    row=self.plan['rows'][rid];source=Path(row['source'])
                    item={'shot':shot['id'],'file':rid,'preview_file':next((i for i in shot['rows'] if self.plan['rows'][i].get('format')=='JPEG'),next((i for i in shot['rows'] if self.plan['rows'][i]['kind'] in ('照片','RAW原片')),None)),'source':str(source),'relative':row['relative'],'date':shot['date'],'size':row['size'],'backup':'','eligible':False,'reason':''}
                    with self.lock:self.state['current']=('生成清单：' if direct else '核对备份：')+row['relative']
                    try:
                        if source.resolve()!=source or self.source not in source.parents or fingerprint(source)!=row['fingerprint']:raise ValueError('来源扫描后变化，请重新扫描')
                        if direct:
                            item.update(source_fingerprint=row['fingerprint'],eligible=True,reason='直接清理：未检查备份')
                            batch.append(item);continue
                        expected=digest(source,self.cancel.is_set)
                        if fingerprint(source)!=row['fingerprint']:raise ValueError('来源读取时发生变化')
                        candidates=[str(self.destination/row['target'])]+names.get(self.name_key(source),[])+sorted(history.get((expected,row['size']),[]))
                        for candidate in dict.fromkeys(candidates):
                            target=Path(candidate)
                            if not safe_existing(target,self.destination) or not off_volume(source,target):continue
                            before=fingerprint(target)
                            if before[2]!=row['size']:continue
                            if digest(target,self.cancel.is_set)==expected and fingerprint(target)==before:
                                item.update(backup=str(target),backup_fingerprint=before,source_fingerprint=row['fingerprint'],sha256=expected,eligible=True);break
                        if not item['eligible']:item['reason']='未找到内容完全一致且位于另一个磁盘卷的备份'
                    except InterruptedError:raise
                    except (OSError,ValueError) as exc:item['reason']=str(exc)
                    batch.append(item)
                if not all(r['eligible'] for r in batch):
                    for item in batch:
                        item['eligible']=False
                        if not item['reason']:item['reason']='关联文件有变化，整组保留' if direct else '关联的 RAW/JPEG/XMP 未全部确认备份，整组保留'
                result.extend(batch)
            with self.lock:
                self.cleanup.update(phase='review',rows=result,token=secrets.token_urlsafe(24),expires=time.monotonic()+900)
                self.state.update(phase='ready',current='清理预览已生成；尚未删除任何原片')
        except Exception as exc:
            with self.lock:
                self.cleanup.update(phase='error',error=str(exc));self.state.update(phase='ready',current=str(exc))

    def cleanup_page(self,payload):
        with self.lock:
            c=getattr(self,'cleanup',{})
            offset=payload.get('offset',0)
            if type(offset)!=int or offset<0:raise ValueError('无效页码')
            rows=c.get('rows',[]);eligible=[r for r in rows if r.get('eligible')]
            valid=bool(self.plan and c.get('plan_id')==self.plan['id'] and c.get('revision')==self.revision and time.monotonic()<c.get('expires',0))
            return {'mode':c.get('mode','verified'),'phase':c.get('phase','idle'),'error':c.get('error',''),'valid':valid,'token':c.get('token','') if valid else '',
                    'source':str(self.source),'destination':str(self.destination),'dates':c.get('dates',[]),'total':len(rows),'files':len(eligible),'bytes':sum(r['size'] for r in eligible),
                    'blocked':len(rows)-len(eligible),'report':c.get('report',''),'deleted':sum(r.get('status')=='已删除' for r in rows),
                    'rows':[{k:v for k,v in r.items() if k not in ('sha256','source_fingerprint','backup_fingerprint')} for r in rows[offset:offset+200]]}

    def cleanup_execute(self,payload):
        with self.lock:
            self._check(payload,('ready',))
            c=getattr(self,'cleanup',{});count=sum(r.get('eligible',False) for r in c.get('rows',[]))
            if c.get('phase')!='review' or c.get('revision')!=self.revision or c.get('plan_id')!=self.plan['id'] or time.monotonic()>=c.get('expires',0):raise ValueError('清理预览已失效，请重新核对')
            confirmed = (payload.get('confirmed') is True and type(payload.get('count')) is int and payload['count']==count) or payload.get('confirmation')==f'删除 {count} 个文件'
            if not count or not secrets.compare_digest(str(payload.get('token','')),c.get('token','')) or not confirmed:raise ValueError('请预览清单并确认删除')
            if c.get('mode')=='direct':source_card_guard(self.source)
            else:card_guard(self.source,self.destination)
            c.update(phase='deleting',token='');self.cancel.clear();self.state.update(phase='preparing',current='删除前再次检查来源' if c.get('mode')=='direct' else '删除前再次检查备份和来源',error='')
        threading.Thread(target=self._cleanup_execute,daemon=True).start()

    def _cleanup_execute(self):
        from app import fingerprint,digest
        from copy_workflow import safe_existing
        c=self.cleanup;rows=[r for r in c['rows'] if r['eligible']]
        def validate(row,contents=False):
            source,target=Path(row['source']),Path(row['backup'])
            if c.get('mode')=='direct':source_card_guard(self.source)
            else:card_guard(self.source,self.destination)
            if source.resolve()!=source or self.source not in source.parents or fingerprint(source)!=row['source_fingerprint']:raise ValueError('来源变化，停止删除：'+row['relative'])
            if c.get('mode')=='direct':return
            if not safe_existing(target,self.destination) or fingerprint(target)!=row['backup_fingerprint']:raise ValueError('备份缺失或变化，停止删除：'+row['relative'])
            if contents:
                if digest(source,self.cancel.is_set)!=row['sha256'] or digest(target,self.cancel.is_set)!=row['sha256']:raise ValueError('内容核验失败，停止删除')
                validate(row)
        try:
            # Validate the entire selected batch before deleting the first file.
            for row in rows:
                if self.cancel.is_set():raise InterruptedError('已停止，未开始删除')
                with self.lock:self.state['current']='删除前复核：'+row['relative']
                validate(row,True)
            folder=self.store.parent/'cleanup-logs';folder.mkdir(parents=True,exist_ok=True)
            report=folder/(dt.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(4)+'.jsonl')
            c['report']=str(report)
            with report.open('x',encoding='utf-8') as out:
                def record(value):out.write(json.dumps(value,ensure_ascii=False)+'\n');out.flush();os.fsync(out.fileno())
                record({'action':'confirmed_cleanup','mode':c.get('mode','verified'),'dates':c['dates'],'files':len(rows),'source':str(self.source),'backup_root':str(self.destination)})
                for row in rows:
                    if self.cancel.is_set():raise InterruptedError('已停止，未处理文件保留在原文件夹中')
                    validate(row)
                    record({'action':'delete_intent','source':row['source'],'backup':row['backup'],'sha256':row.get('sha256'),'mode':c.get('mode','verified')})
                    # No recursive folder removal, no follow of symbolic links.
                    Path(row['source']).unlink()
                    row['status']='已删除'
                    record({'action':'deleted','source':row['source'],'backup':row['backup']})
            c['phase']='finished'
        except Exception as exc:
            c.update(phase='error',error=str(exc))
        finally:
            # Retire the old plan/token, but keep unchanged source metadata for
            # continued cleanup. Copy requires a fresh scan after any deletion.
            import planner
            with self.lock:
                for row in rows:
                    if 'status' not in row:row['status']='未删除'
                removed={r['source'] for r in rows if r.get('status')=='已删除'}
                remaining=[r for r in self.rows if r['source'] not in removed]
                options={**self.plan['options'],'cleanup_only':True}
                self.rows=remaining;self.plan=planner.build_plan(remaining,options)
                self.history=[];self.revision=self.plan['revision']
                self.state={'phase':'ready','results':[],'current':'清理已结束，可以继续选择照片','warnings':[],'version':3}
                self._publish()
