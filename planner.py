"""Pure in-memory shooting groups. No filesystem writes or image decoding."""
import copy
import hashlib
import json
import os
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
    if os.name == 'nt' and (any(c in value for c in '<>"|?*') or value.endswith(('.', ' ')) or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', value, re.I)):
        raise ValueError('Windows 不支持此文件夹名，请修改名称')
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
        sid = hashlib.sha256(json.dumps(sorted((r['source'],r.get('fingerprint')) for r in items),sort_keys=True).encode()).hexdigest()[:16]
        shots[sid] = {'id':sid, 'rows':[r['id'] for r in items], 'time':reliable.get('capture_time', ''), 'date':reliable['date'], 'reliable':reliable.get('reliable_time',False), 'camera':reliable.get('camera',''), 'parent':str(Path(primary['source']).parent), 'seq':sequence_number(primary['relative']), 'photo':primary['kind'] in PHOTO, 'excluded':False}
        for row in items:
            row['format'] = format_folder(row)
            row['shot'] = sid
            lookup[row['id']] = sid
        return sid
    buckets = {}
    for row in rows:
        if row['kind'] in PHOTO:
            p = Path(row['source'])
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
        p=Path(r['source'])
        for name in {p.name.casefold(),p.stem.casefold()}:
            sidecar_index.setdefault((str(p.parent),name),[]).append(r)
    for row in rows:
        if row['id'] in lookup: continue
        p = Path(row['source']); ext = p.suffix.lower()
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
                # Detect the cadence across midnight, then put every exposure in
                # its actual date. Short midnight fragments remain part of the
                # recognized sequence rather than falling below min_frames.
                dated_runs={}
                for shot in run:
                    dated_runs.setdefault(shot['date'] if options.get('date_only') else '', []).append(shot)
                for segment in dated_runs.values():
                    n+=1
                    name=timelapse_name(segment[0]['time'],segment[-1]['time'])
                    add([s['id'] for s in segment],name,'suspected','间隔规律，待你确认；'+('相机信息缺失，仅依据同一来源目录' if not segment[0]['camera'] else '相机信息一致'),interval)
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
    for g in plan['groups']:
        if g['kind'] in ('suspected','timelapse','unknown'):
            first=plan['shots'][g['shots'][0]]
            primary=plan['rows'][first['rows'][0]]
            g['sequence_key']=hashlib.sha256((first['parent']+'\0'+str(first['time'])+'\0'+str(first['seq'])+'\0'+sequence_member(primary)).encode()).hexdigest()
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
        if opts.get('date_only') and g['kind']=='ordinary' and any(r['kind'] in PHOTO for r in allfiles) and g['name']!='普通照片':
            parts.append('普通照片')
        if opts.get('mode')!='legacy' and opts.get('separate_timelapse',False) and g['kind'] in ('suspected','timelapse'):
            parts.append('延时摄影')
        g['folder']=Path(*parts,*group_parts).as_posix()
        for sid in g['shots']:
            shot=plan['shots'][sid]
            for rid in shot['rows']:
                r=plan['rows'][rid]
                r['group']=g['id'];r['excluded']=shot['excluded']
                r['target']=Path(g['folder'],r['format'],Path(r['source']).name).as_posix()
    if plan['options'].get('layout_version') == 2:
        refresh_layout(plan)
    else:
        folders=[g['folder'].casefold() for g in plan['groups']]
        if len(folders)!=len(set(folders)):raise ValueError('同一日期存在同名文件夹，请改名或合并')


def category(plan, group):
    if group['kind'] in ('suspected','timelapse'): return '延时摄影'
    if group['kind']=='unknown': return '时间待确认'
    kinds={plan['rows'][rid]['kind'] for sid in group['shots'] for rid in plan['shots'][sid]['rows']}
    return '普通照片' if kinds & PHOTO else '伴随待确认' if kinds=={'伴随文件'} else next(iter(kinds)) if len(kinds)==1 else '其他文件'


def sequence_member(row):
    # A replacement at the same pathname is not evidence of the old sequence.
    fp=row.get('fingerprint',[])
    return hashlib.sha256(json.dumps([row['source'],fp[2:4],row.get('capture_time'),row.get('camera')],ensure_ascii=False).encode()).hexdigest()


def refresh_layout(plan):
    """Layout depends on the complete scan, never the current copy subset."""
    dates={}
    for g in plan['groups']:
        for sid in g['shots']: dates.setdefault(plan['shots'][sid]['date'],set()).add(category(plan,g))
    routes=plan.get('archive_routes',{})
    for g in plan['groups']:
        cat=category(plan,g)
        seq=g.get('sequence_key','')
        for sid in g['shots']:
            shot=plan['shots'][sid];date=shot['date'];prefix=Path(date[:4],date)
            saved=routes.get(date,{}).get(cat)
            if saved is not None: prefix=Path(saved)
            elif len(dates[date])!=1 or cat not in ('普通照片','延时摄影','视频'): prefix/=cat
            if g['kind'] in ('suspected','timelapse','unknown'):
                # A source-specific suffix distinguishes equal-time sequences.
                name=g['name']+'_'+seq[:8] if seq else g['name']
                name=plan.get('sequence_routes',{}).get(seq,name)
                prefix/=name
            g['folder']=prefix.as_posix()
            for rid in shot['rows']:
                row=plan['rows'][rid];folder=prefix
                if plan['options'].get('separate_formats',True) and shot['photo']:folder/=row['format']
                row['target']=str(folder/Path(row['source']).name)
                row['archive_category']=cat;row['sequence_key']=seq


def reconcile_scan(plan, previous):
    """Retain unchanged shot identities, choice and corrections across source edits."""
    old=previous['shots'];common=set(old)&set(plan['shots'])
    old_groups={sid:g for g in previous['groups'] for sid in g['shots']}
    # Keep original sequences/corrections separate from newly detected material.
    kept=[]
    for g in previous['groups']:
        ids=[sid for sid in g['shots'] if sid in common]
        if ids:
            item=copy.deepcopy(g);item['shots']=ids
            if item.get('sequence_key'):item['auto_name']=False
            kept.append(item)
    for g in plan['groups']:
        shared=[sid for sid in g['shots'] if sid in common]
        added=[sid for sid in g['shots'] if sid not in common]
        prior=[old_groups[sid] for sid in shared]
        sequences={item.get('sequence_key') for item in prior}
        # Detection established one continuous run in this exact source stream.
        # Continue only one known sequence; never bridge corrected or distinct runs.
        if (added and prior and g['kind'] in ('suspected','timelapse') and len(sequences)==1
                and None not in sequences and all(item['kind'] in ('suspected','timelapse') for item in prior)):
            target=next((item for item in kept if item.get('sequence_key') in sequences),None)
            if target is not None:target['shots'].extend(added);continue
        g['shots']=added
        if added:kept.append(g)
    # Ordinary groups can share a date/category path; sequence groups remain distinct.
    plan['groups']=kept
    if previous.get('scope',{}).get('mode','all')=='all':chosen=common
    else:chosen=common & set(previous['scope'].get('shots',[]))
    plan['scope']={'mode':'photos','shots':sorted(chosen)}
    plan['copy_format']=previous.get('copy_format','both')
    refresh(plan)

def catalog_revision(plan):
    """Content identity excludes selection, copy mode and optimistic write revision."""
    rows={rid:{k:r.get(k) for k in ('source','relative','fingerprint','size','kind','format','shot','group','target','excluded','capture_time','date','reliable_time','camera','warning')} for rid,r in plan['rows'].items()}
    groups=[{k:g.get(k) for k in ('id','name','kind','shots','folder','note','interval','auto_name')} for g in plan['groups']]
    value={'id':plan['id'],'rows':rows,'shots':plan['shots'],'groups':groups}
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def reclassify_photos(plan, ids, kind):
    """Move only explicitly selected shots, retaining original sequence boundaries."""
    if kind not in ('ordinary','timelapse') or not ids or any(not plan['shots'][sid]['photo'] or plan['shots'][sid]['excluded'] for sid in ids):
        raise ValueError('请选择当前浏览范围内可调整的照片')
    original=list(plan['groups'])
    for group in original:
        selected=[sid for sid in group['shots'] if sid in ids]
        if not selected:continue
        # Existing confirmed sequences keep their boundaries and names.
        if group['kind']==kind:continue
        dated={}
        for sid in selected:
            shot=plan['shots'][sid]
            dated.setdefault((shot['date'],shot['parent'] if kind=='timelapse' else ''),[]).append(sid)
        group['shots']=[sid for sid in group['shots'] if sid not in ids]
        for (date,parent), members in dated.items():
            target=None
            if kind=='ordinary':
                target=next((g for g in plan['groups'] if g['kind']=='ordinary' and g['name']=='普通照片' and g.get('date')==date and all(plan['shots'][sid]['photo'] and plan['shots'][sid]['date']==date for sid in g['shots'])),None)
            if target is None:
                ordered=sorted_shots(plan,members)
                first,last=(plan['shots'][ordered[i]] for i in (0,-1))
                target={'id':ident(),'name':'普通照片' if kind=='ordinary' else timelapse_name(first['time'] or date,last['time'] or date),'kind':kind,'shots':[],'interval':0,'note':'用户标记的'+('普通照片' if kind=='ordinary' else '延时摄影'),'auto_name':kind=='timelapse','date':date}
                if kind=='timelapse':target['sequence_key']=group.get('sequence_key') or hashlib.sha256((group['id']+'\0'+date+'\0'+parent+'\0'+ordered[0]).encode()).hexdigest()
                plan['groups'].append(target)
            target['shots'].extend(members)
    plan['groups']=[g for g in plan['groups'] if g['shots']]


def edit(plan, payload):
    """Transactional edits, with caller storing prior plan for undo."""
    candidate=copy.deepcopy(plan); groups={g['id']:g for g in candidate['groups']}
    action=payload.get('action');gid=payload.get('group');g=groups.get(gid)
    if action in {'rename','kind','split','merge'} and not g:raise ValueError('请先选择拍摄组')
    if candidate['options'].get('date_only') and action in ('rename','split') and g and g['kind']=='ordinary':
        raise ValueError('普通照片按日期归档，仅延时或待确认序列支持改名和拆分')
    ids=payload.get('shots',[])
    if not isinstance(ids,list) or any(s not in candidate['shots'] for s in ids):raise ValueError('照片选择已失效')
    ids=set(ids)
    if action=='photo_kind':
        reclassify_photos(candidate,ids,payload.get('kind'))
    elif action=='rename':
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

def page(plan,gid,page_number=0,page_size=40,date=None):
    g=next((g for g in plan['groups'] if g['id']==gid),None)
    if not g:raise ValueError('拍摄组不存在')
    if page_number<0:raise ValueError('页码无效')
    ids=[sid for sid in g['shots'] if date is None or plan['shots'][sid]['photo'] and plan['shots'][sid]['date']==date];result=[]
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


def matching_photo_ids(plan, filters):
    """One validated filter shared by catalog and atomic selection; no copy-format filtering."""
    if not isinstance(filters,dict) or set(filters)-{'date','kind','query','groups'}:raise ValueError('照片筛选无效')
    date=filters.get('date','');kind=filters.get('kind','all');query=filters.get('query','');groups=filters.get('groups')
    if not isinstance(date,str) or date and date not in {s['date'] for s in plan['shots'].values() if s['photo']}:raise ValueError('拍摄日期无效')
    if kind not in ('all','ordinary','timelapse') or not isinstance(query,str) or len(query)>500:raise ValueError('照片筛选无效')
    group_map={g['id']:g for g in plan['groups']}
    if groups is not None and (not isinstance(groups,list) or any(not isinstance(g,str) or g not in group_map for g in groups)):raise ValueError('拍摄组筛选无效')
    result=[];query=query.casefold()
    for sid in sorted_shots(plan,plan['shots']):
        shot=plan['shots'][sid]
        if not shot['photo'] or date and shot['date']!=date:continue
        row=plan['rows'][shot['rows'][0]];group=group_map.get(row['group'])
        if not group or groups is not None and group['id'] not in groups:continue
        is_time=group['kind'] in ('suspected','timelapse')
        if kind!='all' and (kind=='timelapse')!=is_time:continue
        if query and query not in (' '.join(plan['rows'][rid]['relative'] for rid in shot['rows'])+' '+shot['date']+' '+group['name']).casefold():continue
        result.append(sid)
    return result

def photo_catalog(plan, filters):
    result=[];groups={g['id']:g for g in plan['groups']}
    for sid in matching_photo_ids(plan,filters):
        shot=plan['shots'][sid];files=[{k:v for k,v in plan['rows'][rid].items() if k not in ('fingerprint','source','source_hash')} for rid in shot['rows']]
        group=groups[files[0]['group']]
        result.append({k:v for k,v in shot.items() if k!='rows'}|{'files':files,'lhGroup':group['id'],'group_kind':group['kind'],'group_name':group['name']})
    return result
