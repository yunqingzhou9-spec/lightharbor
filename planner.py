"""Pure in-memory shooting groups. No filesystem writes or image decoding."""
import copy
import datetime as dt
import math
from pathlib import Path
import re
import statistics
import uuid

PHOTO = {'照片', 'RAW原片'}

def ident(): return uuid.uuid4().hex[:16]

def folder_name(value):
    value = str(value).strip()
    if not value or value.startswith('.') or any(c in value for c in '/\\:') or any(ord(c) < 32 for c in value) or len(value.encode('utf-8')) > 180:
        raise ValueError('文件夹名称不能为空、以点开头、包含 / \\ : 或超过 180 字节')
    return value

def timelapse_name(start, end):
    first, last = dt.datetime.fromisoformat(start), dt.datetime.fromisoformat(end)
    tail = last.strftime('%H时%M分%S秒') if first.date() == last.date() else last.strftime('%Y-%m-%d_%H时%M分%S秒')
    return '延时_' + first.strftime('%Y-%m-%d_%H时%M分%S秒') + '至' + tail

def sequence_number(name):
    m = re.search(r'(\d+)$', Path(name).stem)
    return int(m.group(1)) if m else None

def format_folder(row):
    ext = Path(row['source']).suffix.lower()
    if row['kind'] == 'RAW原片': return 'RAW'
    if ext in {'.jpg', '.jpeg'}: return 'JPEG'
    return {'照片':'其他照片', '视频':'视频', '伴随文件':'伴随待确认'}.get(row['kind'], row['kind'])

def make_shots(rows):
    shots, lookup = {}, {}
    def create(items):
        primary = sorted(items, key=lambda r: r['kind'] != 'RAW原片')[0]
        reliable = next((r for r in items if r.get('reliable_time')), primary)
        sid = ident()
        shots[sid] = {'id':sid, 'rows':[r['id'] for r in items], 'time':reliable.get('capture_time', ''), 'date':reliable['date'], 'reliable':reliable.get('reliable_time',False), 'camera':reliable.get('camera',''), 'parent':str(Path(primary['relative']).parent), 'seq':sequence_number(primary['relative']), 'photo':primary['kind'] in PHOTO, 'excluded':False}
        for row in items:
            row['format'] = format_folder(row)
            row['shot'] = sid
            lookup[row['id']] = sid
        return sid
    buckets = {}
    for row in rows:
        if row['kind'] in PHOTO:
            p = Path(row['relative'])
            buckets.setdefault((str(p.parent),p.stem.casefold()), []).append(row)
    for items in buckets.values():
        # Only pair unambiguous RAW + one rendered photo from the same source folder.
        raws = [r for r in items if r['kind']=='RAW原片']
        rendered = [r for r in items if r['kind']=='照片']
        times = [r['capture_time'] for r in items if r.get('reliable_time')]
        cameras = {r.get('camera') for r in items if r.get('camera')}
        close = not times or (dt.datetime.fromisoformat(max(times))-dt.datetime.fromisoformat(min(times))).total_seconds() <= 2
        if len(raws)==1 and len(rendered)==1 and close and len(cameras)<=1:
            create(items)
        else:
            for row in items: create([row])
    primary_rows = [r for r in rows if r['kind'] in PHOTO]
    sidecar_index = {}
    for r in primary_rows:
        p=Path(r['relative'])
        for name in {p.name.casefold(),p.stem.casefold()}:
            sidecar_index.setdefault((str(p.parent),name),[]).append(r)
    for row in rows:
        if row['id'] in lookup: continue
        p = Path(row['relative']); ext = p.suffix.lower()
        if ext in {'.xmp','.aae'}:
            candidates = sidecar_index.get((str(p.parent),p.stem.casefold()),[])
            if ext=='.xmp' and any(r['kind']=='RAW原片' for r in candidates): candidates=[r for r in candidates if r['kind']=='RAW原片']
            if ext=='.aae': candidates=[r for r in candidates if r['kind']=='照片']
            if len(candidates)==1:
                peer=candidates[0]; sid=lookup[peer['id']]
                row.update(format=peer['format'],shot=sid)
                shots[sid]['rows'].append(row['id']); lookup[row['id']]=sid
                continue
        create([row])
    return shots

def sorted_shots(plan, ids):
    return sorted(ids,key=lambda sid:(plan['shots'][sid]['time'] or plan['shots'][sid]['date'],plan['rows'][plan['shots'][sid]['rows'][0]]['relative']))

def regular_runs(shots, minimum, tolerance):
    """Conservative runs: stable >=1s cadence, allow one/two missed exposures."""
    found=[]
    run=[]; intervals=[]; direct=0
    def finish():
        if len(run)>=minimum and direct >= .75 * max(1,len(run)-1):
            found.append((list(run),statistics.median(intervals)))
    for shot in shots:
        if not run: run=[shot]; continue
        delta=(dt.datetime.fromisoformat(shot['time'])-dt.datetime.fromisoformat(run[-1]['time'])).total_seconds()
        numbered = shot['seq'] is None or run[-1]['seq'] is None or shot['seq']>run[-1]['seq'] or (run[-1]['seq']>=9990 and shot['seq']<10)
        baseline=statistics.median(intervals[-40:]) if intervals else delta
        multiple=round(delta/baseline) if baseline>0 else 0
        fits=1<=baseline<=3600 and 1<=delta<=10800 and multiple in (1,2,3) and abs(delta/multiple-baseline)<=max(.15, baseline*tolerance) and numbered
        if fits:
            run.append(shot)
            if multiple==1: intervals.append(delta); direct+=1
        else:
            finish();run=[shot];intervals=[];direct=0
    finish()
    return found

def build_plan(rows, options):
    minimum=int(options.get('min_frames',30)); tolerance=float(options.get('tolerance',20))/100
    if not 3<=minimum<=100000 or not .05<=tolerance<=.6: raise ValueError('连续张数范围 3–100000，间隔容差范围 5–60%')
    rows=copy.deepcopy(rows)
    for i,r in enumerate(rows): r['id']=str(i)
    plan={'id':ident(),'revision':1,'options':dict(options),'rows':{r['id']:r for r in rows},'shots':make_shots(rows),'groups':[]}
    assigned=set(); streams={}
    for shot in plan['shots'].values():
        if shot['photo'] and shot['reliable']:
            streams.setdefault((shot['camera'],shot['parent']),[]).append(shot)
    def add(ids,name,kind,note='',interval=0):
        plan['groups'].append({'id':ident(),'name':name,'kind':kind,'shots':sorted_shots(plan,ids),'note':note,'interval':interval})
        assigned.update(ids)
    n=0
    if options.get('detect',True):
        for stream in streams.values():
            stream.sort(key=lambda s:(s['time'], s['seq'] if s['seq'] is not None else -1))
            for run,interval in regular_runs(stream,minimum,tolerance):
                n+=1
                name=timelapse_name(run[0]['time'],run[-1]['time'])
                add([s['id'] for s in run],name,'suspected','间隔规律，待你确认；'+('相机信息缺失，仅依据同一来源目录' if not run[0]['camera'] else '相机信息一致'),interval)
                plan['groups'][-1]['auto_name']=True
    missing={}
    for sid,shot in plan['shots'].items():
        if sid not in assigned and shot['photo'] and not shot['reliable']: missing.setdefault((shot['parent'],shot['date']),[]).append(sid)
    for ids in missing.values():
        if options.get('detect',True) and len(ids)>=minimum:
            n+=1;add(ids,f'时间待确认_{n:02d}','unknown','缺少可靠拍摄时间，不自动认定为延时。可手动调整类型。')
    rest={}
    for sid,shot in plan['shots'].items():
        if sid in assigned:continue
        primary=plan['rows'][shot['rows'][0]]
        label='普通照片' if shot['photo'] else ('伴随待确认' if primary['kind']=='伴随文件' else primary['kind'])
        tags=[]
        if options.get('mode')=='legacy':
            members=[plan['rows'][rid] for rid in shot['rows'] if plan['rows'][rid]['kind'] in PHOTO] or [primary]
            if options.get('location'):
                values=sorted({r['location'] for r in members if r['location']!='位置未知'})
                tags.append(values[0] if len(values)==1 else '多地点' if values else '位置未知')
            if options.get('scene'):
                values=sorted({r['scene'] for r in members if r['scene'] not in ('未启用','不适用','景色待确认')})
                tags.append(values[0] if len(values)==1 else '混合景色' if values else members[0]['scene'])
        rest.setdefault((shot['date'] if options.get('date',True) else '',label,tuple(tags)),[]).append(sid)
    for (_,label,_),ids in sorted(rest.items()):add(ids,label,'ordinary')
    refresh(plan)
    return plan

def refresh(plan):
    used_names={g['name'].casefold() for g in plan['groups'] if not g.get('auto_name')}
    for g in plan['groups']:
        g['shots']=sorted_shots(plan,g['shots'])
        live=[plan['shots'][sid] for sid in g['shots'] if not plan['shots'][sid]['excluded']]
        base=live or [plan['shots'][sid] for sid in g['shots']]
        g['date']=min(s['date'] for s in base)
        g['start']=min((s['time'] or s['date']) for s in base)
        g['end']=max((s['time'] or s['date']) for s in base)
        if g.get('auto_name'):
            name=timelapse_name(g['start'],g['end']); unique=name; suffix=2
            while unique.casefold() in used_names:
                unique=f'{name}_{suffix:02d}';suffix+=1
            g['name']=unique;used_names.add(unique.casefold())
        files=[plan['rows'][rid] for s in live for rid in s['rows']]
        allfiles=[plan['rows'][rid] for sid in g['shots'] for rid in plan['shots'][sid]['rows']]
        media=[r for r in allfiles if r['kind'] in PHOTO] or allfiles
        g['locations']=sorted({r['location'] for r in media if r['location']!='位置未知'}) or ['位置未知']
        g['scenes']=sorted({r['scene'] for r in media if r['scene'] not in ('不适用','未启用','景色待确认')}) or [next((r['scene'] for r in media),'不适用')]

        g.update(frames=len(live),total_frames=len(base) if not live else len(g['shots']),files=len(files),bytes=sum(r['size'] for r in files))
        opts=plan['options']; parts=[g['date'][:4],g['date']] if opts.get('date',True) else []
        group_parts=[g['name']]
        if opts.get('mode')=='legacy':
            if opts.get('location'):parts += [g['locations'][0] if len(g['locations'])==1 else '多地点']
            if opts.get('scene'):parts += [g['scenes'][0] if len(g['scenes'])==1 else '混合景色']
            if opts.get('type',True):
                kinds={r['kind'] for r in allfiles}
                category=('延时摄影' if g['kind'] in ('suspected','timelapse') else
                          '时间待确认' if g['kind']=='unknown' else
                          '普通照片' if kinds & PHOTO else
                          '伴随待确认' if kinds=={'伴随文件'} else
                          next(iter(kinds)) if len(kinds)==1 else '其他文件')
                parts.append(category)
                if g['name']==category:group_parts=[]
        g['folder']=str(Path(*parts,*group_parts))
        for sid in g['shots']:
            shot=plan['shots'][sid]
            for rid in shot['rows']:
                r=plan['rows'][rid]
                r['group']=g['id'];r['excluded']=shot['excluded']
                r['target']=str(Path(g['folder'],r['format'],Path(r['source']).name))
    folders=[g['folder'].casefold() for g in plan['groups']]
    if len(folders)!=len(set(folders)):raise ValueError('同一日期存在同名文件夹，请改名或合并')

def edit(plan, payload):
    """Transactional edits, with caller storing prior plan for undo."""
    candidate=copy.deepcopy(plan); groups={g['id']:g for g in candidate['groups']}
    action=payload.get('action');gid=payload.get('group');g=groups.get(gid)
    if action in {'rename','kind','split','merge'} and not g:raise ValueError('请先选择拍摄组')
    ids=payload.get('shots',[])
    if not isinstance(ids,list) or any(s not in candidate['shots'] for s in ids):raise ValueError('照片选择已失效')
    ids=set(ids)
    if action=='rename':
        g['name']=folder_name(payload.get('name',''));g['auto_name']=False
    elif action=='kind':
        if payload.get('kind') not in ('ordinary','timelapse'):raise ValueError('未知类型')
        g['kind']=payload['kind'];g['auto_name']=g.get('auto_name',False) and g['kind']=='timelapse';g['note']='用户确认的'+('延时序列' if g['kind']=='timelapse' else '普通拍摄组')
    elif action=='merge':
        selected=payload.get('groups',[])
        if not isinstance(selected,list) or len(set(selected))<2 or gid not in selected or any(x not in groups for x in selected):raise ValueError('请选择至少两个组，并包含目标组')
        g['shots']=sorted_shots(candidate,[sid for x in selected for sid in groups[x]['shots']])
        g['note']='已手动合并，请核对时间范围';g['interval']=0
        candidate['groups']=[x for x in candidate['groups'] if x['id'] not in selected or x['id']==gid]
    elif action=='split':
        pivot=payload.get('pivot');ordered=sorted_shots(candidate,g['shots'])
        if pivot not in ordered or ordered.index(pivot)==0:raise ValueError('请选择本组第一张之后的照片，作为新段的第一张')
        cut=ordered.index(pivot);new=copy.deepcopy(g);new.update(id=ident(),name=folder_name(payload.get('name',g['name']+'_分段')),shots=ordered[cut:],auto_name=False,interval=0,note='手动拆分的拍摄组')
        g.update(shots=ordered[:cut],interval=0,note='手动拆分的拍摄组');candidate['groups'].append(new)
    elif action=='move':
        if not ids:raise ValueError('请选择照片')
        if payload.get('new_name'):
            target={'id':ident(),'name':folder_name(payload['new_name']),'kind':'ordinary','shots':[],'interval':0,'note':'手动建立的拍摄组'}
            candidate['groups'].append(target)
        else:
            target=groups.get(payload.get('target'))
            if not target:raise ValueError('请选择目标组')
        for item in candidate['groups']:item['shots']=[sid for sid in item['shots'] if sid not in ids]
        target['shots']+=list(ids)
        candidate['groups']=[x for x in candidate['groups'] if x['shots']]
    elif action in ('exclude','restore'):
        if not ids:raise ValueError('请选择照片')
        for sid in ids:candidate['shots'][sid]['excluded']=action=='exclude'
    else:raise ValueError('未知的调整操作')
    candidate['revision']+=1;refresh(candidate);return candidate

def summaries(plan):
    return [{k:v for k,v in g.items() if k!='shots'} for g in plan['groups']]

def page(plan,gid,page_number=0,page_size=40):
    g=next((g for g in plan['groups'] if g['id']==gid),None)
    if not g:raise ValueError('拍摄组不存在')
    if page_number<0:raise ValueError('页码无效')
    ids=g['shots'];result=[]
    for sid in ids[page_number*page_size:(page_number+1)*page_size]:
        s=copy.deepcopy(plan['shots'][sid]);s['files']=[{k:v for k,v in plan['rows'][rid].items() if k not in ('fingerprint','source')} for rid in s.pop('rows')]
        result.append(s)
    tree={}
    for sid in ids:
        for rid in plan['shots'][sid]['rows']:
            r=plan['rows'][rid]
            if r['excluded']:continue
            f=str(Path(r['target']).parent);node=tree.setdefault(f,{'files':0,'bytes':0});node['files']+=1;node['bytes']+=r['size']
    samples=[]
    for index in sorted({0,len(ids)//2,len(ids)-1}):
        if not ids:break
        files=[plan['rows'][rid] for rid in plan['shots'][ids[index]]['rows']]
        photo=next((r for r in files if r['format']=='JPEG'),next((r for r in files if r['kind'] in PHOTO),None))
        if photo:samples.append({'file':photo['id'],'name':photo['relative']})
    return {'samples':samples,'shots':result,'total':len(ids),'page':page_number,'pages':max(1,math.ceil(len(ids)/page_size)),'tree':tree}
