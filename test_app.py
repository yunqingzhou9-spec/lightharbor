import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
import app


class OrganizerTests(unittest.TestCase):
    def test_preview_quality_validation_and_changed_source_guard(self):
        p=self.fixture('one.jpg');rows=app.scan(self.src,self.dest,{'detect':False})[0]
        organizer=app.Organizer(self.root/'tasks');organizer.rows=rows;organizer.source=self.src;organizer.destination=self.dest
        organizer.plan=app.planner.build_plan(rows,{'detect':False});organizer.revision=1;organizer.state['phase']='ready';organizer._publish()
        ident={'plan_id':organizer.plan['id'],'revision':1};fid=next(iter(organizer.plan['rows']))
        with self.assertRaises(ValueError):organizer.thumbnail({**ident,'file':fid,'quality':'original'})
        output=type('Result',(),{'stdout':json.dumps({'thumbnail':'example','width':3200,'height':2000})})()
        with patch('app.subprocess.run',return_value=output):
            result=organizer.thumbnail({**ident,'file':fid,'quality':'high'})
        self.assertEqual(result['width'],3200);self.assertEqual(result['preview_source'],'照片预览')
        p.write_bytes(b'changed')
        with self.assertRaises(ValueError):organizer.thumbnail({**ident,'file':fid,'quality':'high'})

    def test_single_photo_kind_does_not_depend_on_copy_selection(self):
        for name in ['pair.jpg','pair.nef','pair.xmp','other.jpg','keep.txt']:self.fixture(name)
        organizer=app.Organizer(self.root/'tasks')
        with patch.object(app.Probe,'read',return_value={'date':'2026:10:12 12:00:00'}):
            organizer.start_scan({'source':str(self.src),'destination':str(self.dest),'options':{'detect':False}})
            for _ in range(200):
                if organizer.snapshot()['phase']!='scanning':break
                time.sleep(.01)
        self.assertEqual(organizer.snapshot()['phase'],'ready')
        ident=lambda:{'plan_id':organizer.plan['id'],'revision':organizer.revision}
        sid=next(sid for sid,s in organizer.plan['shots'].items() if any(organizer.plan['rows'][r]['relative']=='pair.jpg' for r in s['rows']))
        organizer.set_scope({**ident(),'mode':'toggle_shots','filter':{},'checked':False})
        before={p:p.read_bytes() for p in self.src.rglob('*') if p.is_file()}
        scope=organizer.plan['scope'].copy();selected=organizer.active_shots();stale=ident()
        organizer.edit({**ident(),'action':'photo_kind','shot':sid,'kind':'timelapse'})
        self.assertEqual(organizer.plan['scope'],scope);self.assertEqual(organizer.active_shots(),selected)
        moved=next(g for g in organizer.plan['groups'] if sid in g['shots'])
        self.assertEqual(moved['kind'],'timelapse');self.assertEqual(moved['shots'],[sid])
        self.assertEqual(len(organizer.plan['shots'][sid]['rows']),3)
        for row in organizer.plan['shots'][sid]['rows']:self.assertIn('/延时摄影/',organizer.plan['rows'][row]['target'])
        with self.assertRaises(ValueError):organizer.edit({**stale,'action':'photo_kind','shot':sid,'kind':'ordinary'})
        organizer.edit({**ident(),'action':'undo'})
        self.assertEqual(next(g for g in organizer.plan['groups'] if sid in g['shots'])['kind'],'ordinary')
        self.assertEqual(organizer.plan['scope'],scope)
        for invalid in ['missing',[],None,next(s for s,v in organizer.plan['shots'].items() if not v['photo'])]:
            with self.assertRaises(ValueError):organizer.edit({**ident(),'action':'photo_kind','shot':invalid,'kind':'timelapse'})
        self.assertEqual(before,{p:p.read_bytes() for p in self.src.rglob('*') if p.is_file()})

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.src = self.root / 'card'; self.src.mkdir()
        self.dest = self.root / 'archive'; self.dest.mkdir()
    def tearDown(self): self.tmp.cleanup()
    def fixture(self, name='DCIM/item.txt', data=b'original'):
        p = self.src / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data); return p
    def plan(self, options=None): return app.scan(self.src, self.dest, options or {'date': True, 'type': True})[0]
    def test_scan_reports_actual_stages_and_file_count(self):
        for n in range(3): self.fixture(f'photo-{n}.jpg')
        organizer=app.Organizer(self.root/'tasks'); observed=[]
        original_plan=app.planner.build_plan
        def read(*args):
            snapshot=organizer.snapshot(); observed.append((snapshot['scan_stage'],snapshot['count']))
            return {'date':'2026:01:02 12:00:00'}
        def build(rows,options):
            snapshot=organizer.snapshot(); observed.append((snapshot['scan_stage'],snapshot['count']))
            return original_plan(rows,options)
        def imports():
            snapshot=organizer.snapshot(); observed.append((snapshot['scan_stage'],snapshot['count']))
        with patch.object(app.Probe,'read',side_effect=read),patch.object(app.planner,'build_plan',side_effect=build),patch.object(organizer,'assess_imports',side_effect=imports):
            organizer.start_scan({'source':str(self.src),'destination':str(self.dest),'options':{'detect':False}})
            for _ in range(200):
                if organizer.snapshot()['phase']!='scanning': break
                time.sleep(.01)
        self.assertEqual(organizer.snapshot()['phase'],'ready')
        self.assertEqual(observed,[('reading',0),('reading',1),('reading',2),('grouping',3),('imports',3)])
        self.assertFalse(list(self.dest.iterdir()))

    def test_photo_date_index_paging_and_scope_ignore_output_folder_layout(self):
        for name in ['10_a.jpg','10_a.xmp','11_a.jpg','11_b.jpg','keep.txt']:self.fixture(name)
        organizer=app.Organizer(self.root/'tasks')
        def metadata(probe,path,scene):return {'date':'2026:10:'+('10' if Path(path).name.startswith('10') else '11')+' 12:00:00'}
        with patch.object(app.Probe,'read',metadata):
            organizer.start_scan({'source':str(self.src),'destination':str(self.dest),'options':{'date':False,'detect':False}})
            for _ in range(200):
                if organizer.snapshot()['phase']!='scanning':break
                time.sleep(.01)
        state=organizer.snapshot();self.assertEqual(state['phase'],'ready')
        self.assertEqual([(d['date'],d['shots'],d['files']) for d in state['photo_dates']],[('2026-10-11',2,2),('2026-10-10',1,2)])
        identity=lambda:{'plan_id':organizer.plan['id'],'revision':organizer.revision}
        visible=[]
        for g in state['groups']:
            rows=organizer.files({**identity(),'group':g['id'],'date':'2026-10-11'})['shots'];visible.extend(rows)
        self.assertEqual(len(visible),2);self.assertTrue(all(row['date']=='2026-10-11' for row in visible))
        organizer.set_scope({**identity(),'mode':'toggle_shots','date':'2026-10-10','checked':False})
        self.assertEqual(organizer.snapshot()['included'],3) # other day + unrelated text retained
        organizer.set_scope({**identity(),'mode':'toggle_shots','date':'2026-10-10','checked':True})
        self.assertEqual(organizer.snapshot()['included'],5)
        with self.assertRaises(ValueError):organizer.set_scope({**identity(),'mode':'toggle_shots','date':'2026-12-31','checked':False})
        self.assertFalse(list(self.dest.iterdir()))

    def test_scan_never_writes_and_nested_roots_rejected(self):
        p = self.fixture(); before = app.fingerprint(p)
        rows = self.plan()
        self.assertEqual(app.fingerprint(p), before)
        self.assertEqual(len(rows), 1)
        self.assertFalse(list(self.dest.iterdir()))
        for dest in (self.src, self.src/'inside', self.root):
            with self.assertRaises(ValueError): app.valid_roots(self.src, dest)
    def test_copy_checksum_conflict_duplicate_and_repeat(self):
        p = self.fixture(data=b'a'*10000); row = self.plan()[0]; before = app.fingerprint(p)
        target = self.dest / row['target']; target.parent.mkdir(parents=True); target.write_bytes(b'keep this')
        result = app.copy_one(row, self.src, self.dest)
        self.assertEqual(result['status'], '已复制')
        self.assertTrue(result['target'].endswith('__1.txt'))
        self.assertEqual(target.read_bytes(), b'keep this')
        self.assertEqual(Path(result['target']).read_bytes(), p.read_bytes())
        self.assertEqual(app.fingerprint(p), before)
        self.assertEqual(app.copy_one(row, self.src, self.dest)['status'], '重复跳过')
    def test_changed_source_refused(self):
        p = self.fixture(); row = self.plan()[0]; p.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, '变化'): app.copy_one(row, self.src, self.dest)
    def test_symlinks_hidden_and_output_symlink(self):
        p = self.fixture(); (self.src/'link.txt').symlink_to(p); self.fixture('.secret')
        (self.src/'loop').symlink_to(self.src, target_is_directory=True)
        rows = self.plan(); self.assertEqual(len(rows), 1)
        outside = self.root/'outside'; outside.mkdir()
        first = Path(rows[0]['target']).parts[0]; (self.dest/first).symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError): app.copy_one(rows[0], self.src, self.dest)
        self.assertFalse(list(outside.iterdir()))
    def test_scan_ignores_removed_analysis_even_with_stale_flags(self):
        self.fixture('image.jpg')
        meta={'date':'2020:05:06 12:30:40','gps':[-33.86,151.21],'labels':[{'label':'beach','score':.9}]}
        with patch.object(app.Probe, 'read', return_value=meta) as read:
            row = self.plan(dict(date=False,type=False,location=True,scene=True))[0]
        self.assertEqual(read.call_args.args[1],False)
        self.assertEqual(row['target'],'2020/2020-05-06/照片/image.jpg')
        self.assertEqual(row['date_source'],'拍摄日期')
        self.assertEqual((row['location'],row['scene'],row['confidence']),('位置未知','未启用',0))

    def test_new_scan_forces_date_policy_and_retains_supported_options(self):
        self.fixture('image.jpg')
        organizer=app.Organizer(self.root/'tasks')
        stale=dict(mode='legacy',date=False,type=False,separate_timelapse=False,location=True,scene=True,date_only=False,
                   detect=False,min_frames=40,tolerance=15,duplicate_mode='content')
        with patch.object(app.Probe,'read',return_value={'date':'2020:05:06 12:30:40'}):
            organizer.start_scan({'source':str(self.src),'destination':str(self.dest),'options':stale})
            for _ in range(200):
                if organizer.snapshot()['phase']!='scanning':break
                time.sleep(.01)
        state=organizer.snapshot();self.assertEqual(state['phase'],'ready')
        self.assertEqual(organizer.plan['options'],dict(stale,mode='groups',date=True,type=True,separate_timelapse=True,location=False,scene=False,date_only=True,layout_version=2,separate_formats=True))
        self.assertEqual(next(iter(organizer.plan['rows'].values()))['target'],'2020/2020-05-06/JPEG/image.jpg')
        before=json.dumps(organizer.plan,sort_keys=True)
        for action in ('move','merge'):
            with self.assertRaisesRegex(ValueError,'已移除'):
                organizer.edit({'plan_id':organizer.plan['id'],'revision':organizer.revision,'action':action})
        self.assertEqual(json.dumps(organizer.plan,sort_keys=True),before)

    def test_removed_preferences_are_ignored_for_read_and_write(self):
        organizer=app.Organizer(self.root/'tasks')
        remaining=dict(detect=False,min_frames=40,tolerance=15,duplicate_mode='content')
        stale=dict(mode='legacy',date=False,type=False,location=True,scene=True,separate_timelapse=False)
        path=organizer.store.parent/'organizer-preferences.json'
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(dict(remaining,**stale)))
        self.assertEqual(organizer.preferences({}),remaining)
        self.assertEqual(organizer.preferences({'values':dict(remaining,**stale)}),remaining)
        self.assertEqual(json.loads(path.read_text()),remaining)

    def test_old_saved_task_preserves_options_and_targets_on_restore(self):
        self.fixture('image.jpg')
        rows=self.plan()[0:1]
        rows[0].update(location='GPS_A',scene='山景')
        options=dict(mode='legacy',date=False,type=True,location=True,scene=True,detect=False)
        organizer=app.Organizer(self.root/'tasks');organizer.rows=rows;organizer.source=self.src;organizer.destination=self.dest
        organizer.plan=app.planner.build_plan(rows,options);organizer.revision=1;organizer.state['phase']='ready';organizer._publish();organizer.save_task()
        targets={rid:r['target'] for rid,r in organizer.plan['rows'].items()}
        self.assertTrue(all(target.startswith('GPS_A/山景/普通照片/') for target in targets.values()))
        restored=app.Organizer(organizer.store);restored.resume_task({'task':organizer.plan['id']})
        for _ in range(200):
            if restored.snapshot()['phase']!='preparing':break
            time.sleep(.01)
        self.assertEqual(restored.snapshot()['phase'],'ready')
        self.assertEqual(restored.plan['options'],options)
        self.assertEqual({rid:r['target'] for rid,r in restored.plan['rows'].items()},targets)
        restored.set_scope({'plan_id':restored.plan['id'],'revision':restored.revision,'mode':'all'})
        self.assertEqual({rid:r['target'] for rid,r in restored.plan['rows'].items()},targets)
        self.assertFalse(list(self.dest.iterdir()))

    def test_windows_metadata_never_returns_gps_or_scene(self):
        import windows_media
        tags={'EXIF DateTimeOriginal':'2020:05:06 12:30:40','GPS GPSLatitude':object(),'GPS GPSLongitude':object()}
        with patch.object(windows_media,'tags_for',return_value=tags):
            meta=windows_media.metadata('unused.jpg',True)
        self.assertEqual(meta['date'],'2020:05:06 12:30:40')
        self.assertNotIn('gps',meta);self.assertNotIn('labels',meta);self.assertNotIn('warning',meta)

    def test_copy_cancel_removes_partial_only(self):
        self.fixture(data=b'x'*3000000); row=self.plan()[0]
        calls=0
        def cancelled():
            nonlocal calls
            calls+=1
            return calls>4
        with self.assertRaises(InterruptedError): app.copy_one(row,self.src,self.dest,cancelled)
        self.assertFalse([p for p in self.dest.rglob('*') if p.is_file()])
        self.assertEqual(Path(row['source']).stat().st_size,3000000)
    def test_corrupt_copy_removed(self):
        self.fixture(); row=self.plan()[0]
        original_digest=app.digest
        def changed(path, cancelled=lambda:False):
            return original_digest(path,cancelled) if Path(path)==Path(row['source']) else 'corrupt'
        with patch.object(app,'digest',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'校验失败'): app.copy_one(row,self.src,self.dest)
        self.assertFalse([p for p in self.dest.rglob('*') if p.is_file()])
    def test_http_auth_scan_copy_report(self):
        p=self.fixture(); organizer=app.Organizer()
        server=app.LocalServer(('127.0.0.1',0),app.make_handler(organizer,'test-secret'))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        def request(path,body=None,token='test-secret',origin=None):
            headers={'X-Organizer-Token':token,'Content-Type':'application/json'}
            if origin:headers['Origin']=origin
            req=Request(base+path,headers=headers,data=None if body is None else json.dumps(body).encode())
            with urlopen(req, timeout=5) as response:return json.load(response)
        def wait_phase(target):
            for _ in range(200):
                state=request('/api/state')
                if state['phase']==target:return state
                if state['phase']=='error':self.fail(state)
                time.sleep(.02)
            self.fail('timeout')
        try:
            with self.assertRaises(HTTPError) as exc:request('/api/state',token='wrong')
            self.assertEqual(exc.exception.code,403)
            exc.exception.close()
            with self.assertRaises(HTTPError) as exc:request('/api/scan',{},origin='https://other.example')
            exc.exception.close()
            request('/api/scan',{'source':str(self.src),'destination':str(self.dest),'options':{'date':False,'type':False}})
            self.assertEqual(wait_phase('ready')['count'],1)
            ready=wait_phase('ready')
            request('/api/prepare',{'plan_id':ready['plan_id'],'revision':ready['revision']})
            ready=wait_phase('reviewing')
            request('/api/copy',{'plan_id':ready['plan_id'],'revision':ready['revision']})
            state=wait_phase('done');self.assertEqual(state['results'][0]['status'],'已复制')
            record=json.loads(Path(state['report']).read_text().strip())
            self.assertEqual(record['sha256'],app.digest(p))
            self.assertTrue(p.exists())
        finally:server.shutdown();server.server_close();thread.join()
    def test_full_result_totals_independent_of_detail_limit(self):
        organizer = app.Organizer()
        for phase, statuses, included in [
            ('done', ['已复制'] * 1234, 1234),
            ('done', ['失败'] * 3 + ['重复跳过'] * 231 + ['已复制'] * 1000, 1234),
            ('cancelled', ['失败'] * 2 + ['重复跳过'] * 8 + ['已复制'] * 1001, 1234),
            ('cancelled', [], 1234),
        ]:
            with self.subTest(phase=phase, count=len(statuses)):
                organizer.state.update(phase=phase, included=included, results=[{'status': x} for x in statuses])
                snapshot = organizer.snapshot()
                self.assertEqual(snapshot['processed'], len(statuses))
                self.assertEqual(snapshot['remaining'], included-len(statuses))
                self.assertEqual(snapshot['results_total'], len(statuses))
                self.assertEqual(len(snapshot['results']), min(1000, len(statuses)))
                for status in ('已复制', '重复跳过', '失败'):
                    self.assertEqual(snapshot['result_counts'][status], statuses.count(status))
                if snapshot['results']:
                    snapshot['results'][0]['status'] = 'changed'
                    self.assertNotEqual(organizer.state['results'][-min(1000,len(statuses))]['status'], 'changed')
        organizer.state = {'phase': 'scanning', 'results': []}
        self.assertEqual(organizer.snapshot()['processed'], 0)
        self.assertEqual(sum(organizer.snapshot()['result_counts'].values()), 0)

    def test_all_result_pages_and_stale_requests(self):
        organizer=app.Organizer()
        organizer.plan={'id':'p','revision':1}
        organizer.state.update(phase='done',results=[{'status':'已复制','source':str(i)} for i in range(1234)])
        received=[]
        while len(received)<1234:
            page=organizer.result_page({'plan_id':'p','revision':1,'offset':len(received)})
            self.assertLessEqual(len(page['results']),200)
            self.assertEqual(page['total'],1234)
            received.extend(page['results'])
        self.assertEqual([r['source'] for r in received],[str(i) for i in range(1234)])
        for payload in ({'plan_id':'old','revision':1},{'plan_id':'p','revision':2},{'plan_id':'p','revision':1,'offset':-1}):
            with self.assertRaises(ValueError):organizer.result_page(payload)

    def test_unknown_metadata_explicit(self):
        self.fixture('broken.heic',b'not an image')
        row=self.plan({'date':True,'type':True,'location':True,'scene':True})[0]
        self.assertEqual(row['date_source'],'文件修改日期')
        self.assertEqual(row['location'],'位置未知')
        self.assertEqual(row['scene'],'未启用')
        self.assertTrue(row['warning'])

if __name__=='__main__':unittest.main()
