import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import app
import planner


def fixtures(n=35,start='2025-07-09T23:59:00',interval=5,folder='DCIM',offset=0,paired=True,reliable=True):
    rows=[]
    for i in range(n):
        date=dt.datetime.fromisoformat(start)+dt.timedelta(seconds=i*interval)
        for ext,kind in ([('nef','RAW原片'),('jpg','照片'),('xmp','伴随文件')] if paired else [('jpg','照片')]):
            relative=f'{folder}/DSC_{i+offset:04d}.{ext}'
            rows.append({'source':'/source/'+relative,'relative':relative,'kind':kind,'date':date.date().isoformat(),'capture_time':date.isoformat(),'reliable_time':reliable and kind!='伴随文件','camera':'Nikon / Z6 / 123' if kind!='伴随文件' else '', 'location':'位置未知','scene':'未启用','size':10,'warning':'','date_source':'拍摄日期' if reliable else '文件修改日期','fingerprint':[1,1,10,1,1],'target':relative})
    return rows

class PlannerTests(unittest.TestCase):
    def test_pairing_count_midnight_and_raw_jpeg_folders(self):
        p=planner.build_plan(fixtures(),{})
        self.assertEqual(len(p['shots']),35);self.assertEqual(len(p['groups']),1)
        g=p['groups'][0];self.assertEqual(g['kind'],'suspected');self.assertEqual(g['date'],'2025-07-09')
        self.assertEqual(g['files'],105)
        self.assertTrue(all(r['target'].startswith('2025/2025-07-09/延时_') for r in p['rows'].values()))
        for r in p['rows'].values():self.assertIn('/JPEG/' if r['kind']=='照片' else '/RAW/',r['target'])
    def test_readable_sequence_names_and_custom_names(self):
        p=planner.build_plan(fixtures(n=35,start='2025-07-09T10:00:00'),{})
        g=p['groups'][0]
        self.assertEqual(g['name'],'延时_2025-07-09_10时00分00秒至10时02分50秒')
        p=planner.edit(p,{'action':'exclude','shots':[g['shots'][0]]})
        self.assertIn('10时00分05秒至',p['groups'][0]['name'])
        p=planner.edit(p,{'action':'rename','group':g['id'],'name':'海边日落'})
        p=planner.edit(p,{'action':'restore','shots':[g['shots'][0]]})
        self.assertEqual(p['groups'][0]['name'],'海边日落')
        cross=planner.build_plan(fixtures(),{})['groups'][0]['name']
        self.assertEqual(cross,'延时_2025-07-09_23时59分00秒至2025-07-10_00时01分50秒')
        rows=fixtures(folder='A')+fixtures(folder='B')
        names=[g['name'] for g in planner.build_plan(rows,{})['groups']]
        self.assertEqual(len(set(names)),2);self.assertTrue(names[1].endswith('_02'))
        self.assertNotIn(':',cross)

    def test_pairs_do_not_inflate_threshold(self):
        p=planner.build_plan(fixtures(n=20),{})
        self.assertFalse(any(g['kind']=='suspected' for g in p['groups']))
    def test_burst_and_irregular_sequences_not_timelapse(self):
        p=planner.build_plan(fixtures(interval=.1),{})
        self.assertFalse(any(g['kind']=='suspected' for g in p['groups']))
        rows=fixtures(n=40,paired=False)
        t=dt.datetime(2025,7,9)
        for i,r in enumerate(rows):
            t+=dt.timedelta(seconds=[1,7,23,11][i%4]);r['capture_time']=t.isoformat()
        self.assertFalse(any(g['kind']=='suspected' for g in planner.build_plan(rows,{})['groups']))
    def test_gap_camera_and_missing_timestamps(self):
        a=fixtures(n=32,paired=False,start='2025-07-09T10:00:00')
        b=fixtures(n=32,paired=False,start='2025-07-09T12:00:00',offset=32)
        p=planner.build_plan(a+b,{})
        self.assertEqual(sum(g['kind']=='suspected' for g in p['groups']),2)
        missing=planner.build_plan(fixtures(reliable=False,start="2025-07-09T10:00:00"),{})
        self.assertEqual(missing['groups'][0]['kind'],'unknown')
        rows=fixtures(n=40,paired=False)
        for i,r in enumerate(rows):r['camera']='camera A' if i%2 else 'camera B'
        self.assertFalse(any(g['kind']=='suspected' for g in planner.build_plan(rows,{})['groups']))
    def test_missed_frame_tolerance(self):
        rows=fixtures(n=40,paired=False)
        rows.pop(20)
        p=planner.build_plan(rows,{})
        self.assertEqual(p['groups'][0]['kind'],'suspected');self.assertEqual(p['groups'][0]['frames'],39)
    def test_unambiguous_sidecars_only(self):
        rows=fixtures(n=1)
        rows.append(dict(rows[1],source='/source/DCIM/DSC_0000.heic',relative='DCIM/DSC_0000.heic'))
        rows.append(dict(rows[2],source='/source/DCIM/DSC_0000.aae',relative='DCIM/DSC_0000.aae'))
        p=planner.build_plan(rows,{})
        aae=next(r for r in p['rows'].values() if r['relative'].endswith('.aae'))
        self.assertEqual(aae['format'],'伴随待确认')
        xmp=next(r for r in p['rows'].values() if r['relative'].endswith('.xmp'))
        raw=next(r for r in p['rows'].values() if r['kind']=='RAW原片')
        self.assertEqual(raw['shot'],xmp['shot'])
    def test_edit_split_merge_move_exclude_and_restore(self):
        p=planner.build_plan(fixtures(),{});g=p['groups'][0];pivot=g['shots'][10]
        p=planner.edit(p,{'action':'split','group':g['id'],'pivot':pivot,'name':'后半段'})
        self.assertEqual([g['frames'] for g in p['groups']],[10,25])
        p=planner.edit(p,{'action':'merge','group':g['id'],'groups':[x['id'] for x in p['groups']]})
        self.assertEqual(len(p['groups']),1)
        sid=p['groups'][0]['shots'][0]
        p=planner.edit(p,{'action':'move','shots':[sid],'new_name':'普通补充'})
        moved=p['groups'][-1];self.assertEqual(moved['files'],3)
        p=planner.edit(p,{'action':'exclude','shots':[sid]})
        self.assertEqual(p['groups'][-1]['files'],0)
        self.assertTrue(all(p['rows'][r]['excluded'] for r in p['shots'][sid]['rows']))
        p=planner.edit(p,{'action':'restore','shots':[sid]})
        self.assertEqual(p['groups'][-1]['files'],3)
    def test_invalid_edits_transactional_and_complete_pagination(self):
        p=planner.build_plan(fixtures(n=100,paired=False),{});before=copy.deepcopy(p);gid=p['groups'][0]['id']
        for name in ('../escape','x/y','.hidden',''):
            with self.assertRaises(ValueError):planner.edit(p,{'action':'rename','group':gid,'name':name})
        self.assertEqual(before,p)
        seen=[s['id'] for page in range(3) for s in planner.page(p,gid,page)['shots']]
        self.assertEqual(len(set(seen)),100)
    def test_no_date_layer_and_sidecars_do_not_pollute_group_tags(self):
        rows=fixtures(n=2)
        for r in rows:
            if r['kind']!='伴随文件':r.update(location='GPS_+31.22_+121.46',scene='山景')
            else:r.update(location='位置未知',scene='不适用')
        p=planner.build_plan(rows,{'date':False})
        self.assertEqual(len(p['groups']),1)
        self.assertEqual(p['groups'][0]['folder'],'普通照片')
        self.assertEqual(p['groups'][0]['locations'],['GPS_+31.22_+121.46'])
        self.assertEqual(p['groups'][0]['scenes'],['山景'])

    def test_location_scene_before_shooting_type(self):
        rows=fixtures(start='2025-07-09T10:00:00')
        for r in rows:
            if r['kind']!='伴随文件':r.update(location='GPS_A',scene='山景')
        p=planner.build_plan(rows,dict(mode='legacy',location=True,scene=True))
        self.assertTrue(p['groups'][0]['folder'].startswith('2025/2025-07-09/GPS_A/山景/延时摄影/'))
        self.assertEqual(len(p['groups']),1)
        original=planner.build_plan(rows,dict(mode='groups',location=True,scene=True))
        self.assertNotIn('GPS_A',original['groups'][0]['folder'])

    def test_ordinary_photos_separate_by_labels_without_duplicate_folder(self):
        rows=fixtures(n=2,start='2025-07-09T10:00:00')
        for i,r in enumerate(rows):
            if r['kind']!='伴随文件':r.update(location='GPS_A' if i<3 else 'GPS_B',scene='山景' if i<3 else '海滩与海洋')
        for opts,expected in [({'location':True},['GPS_A','GPS_B']),({'scene':True},['山景','海滩与海洋'])]:
            p=planner.build_plan(rows,dict(mode='legacy',**opts))
            self.assertEqual({g['folder'] for g in p['groups']},{f'2025/2025-07-09/{tag}/普通照片' for tag in expected})
            self.assertTrue(all(g['files']==3 for g in p['groups']))
        p=planner.build_plan(rows,dict(mode='groups',location=True,scene=True))
        self.assertEqual(len(p['groups']),1)

    def test_legacy_uses_group_labels_never_splits_sequence(self):
        rows=fixtures()
        for i,r in enumerate(rows):r['scene']='山景' if i%2 else '森林与植物'
        p=planner.build_plan(rows,dict(mode='legacy',date=True,type=True,scene=True))
        self.assertEqual(len(p['groups']),1)
        self.assertIn('/混合景色/',p['groups'][0]['folder'])

class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.src=self.root/'source';self.src.mkdir();self.dest=self.root/'target'
        rows=fixtures(n=3,paired=True)
        for i,r in enumerate(rows):
            p=self.src/r['relative'];p.parent.mkdir(exist_ok=True);p.write_bytes(f'content-{i}'.encode())
            r.update(source=str(p),size=p.stat().st_size,fingerprint=app.fingerprint(p))
        self.org=app.Organizer();self.org.rows=rows;self.org.source=self.src;self.org.destination=self.dest
        self.org.plan=planner.build_plan(rows,{'min_frames':3,'duplicate_mode':'content'});self.org.revision=1;self.org.state.update(phase='ready');self.org._publish()
    def tearDown(self):self.tmp.cleanup()
    def identity(self):return {'plan_id':self.org.plan['id'],'revision':self.org.plan['revision']}
    def wait(self,phase):
        for _ in range(500):
            s=self.org.snapshot()
            if s['phase']==phase:return s
            if s.get('error'):self.fail(s)
            time.sleep(.005)
        self.fail(self.org.snapshot())
    def test_no_output_before_final_confirmation_and_stale_revision(self):
        old=self.identity();g=self.org.plan['groups'][0]
        self.org.edit(dict(old,action='rename',group=g['id'],name='日落'))
        self.assertFalse(self.dest.exists())
        with self.assertRaises(ValueError):self.org.prepare(old)
        with self.assertRaises(ValueError):self.org.start_copy(self.identity())
        self.org.prepare(self.identity());self.wait('reviewing');self.assertFalse(self.dest.exists())
        with self.assertRaises(ValueError):self.org.start_copy(old)
        self.org.start_copy(self.identity());s=self.wait('done')
        self.assertEqual(len(s['results']),9);self.assertTrue(all(x['status']=='已复制' for x in s['results']))
        self.assertTrue(all(Path(r['source']).exists() for r in self.org.rows))
    def test_coordinate_conflict_suffix_for_all_formats_and_sidecar(self):
        first=next(r for r in self.org.plan['rows'].values() if r['kind']=='RAW原片')
        target=self.dest/first['target'];target.parent.mkdir(parents=True);target.write_bytes(b'keep existing')
        self.org.prepare(self.identity());self.wait('reviewing')
        shot=self.org.plan['shots'][first['shot']]
        self.assertTrue(all('__1.' in self.org.plan['rows'][rid]['target'] for rid in shot['rows']))
        self.org.start_copy(self.identity());self.wait('done');self.assertEqual(target.read_bytes(),b'keep existing')
    def test_exclusion_undo_invalidates_confirmation(self):
        first=self.org.plan['groups'][0]['shots'][0]
        self.org.edit(dict(self.identity(),action='exclude',shots=[first]));self.assertEqual(self.org.state['included'],6)
        self.org.prepare(self.identity());self.wait('reviewing');old=self.identity()
        self.org.edit(dict(self.identity(),action='undo'))
        self.assertEqual(self.org.state['included'],9);self.assertEqual(self.org.state['phase'],'ready')
        with self.assertRaises(ValueError):self.org.start_copy(old)
        self.assertFalse(self.dest.exists())
    def test_source_changed_preflight_rejects_without_writing(self):
        Path(self.org.rows[0]['source']).write_bytes(b'changed')
        self.org._prepare()
        self.assertEqual(self.org.state['phase'],'ready');self.assertIn('变化',self.org.state['error']);self.assertFalse(self.dest.exists())
    def test_conflict_after_confirmation_never_silently_renames(self):
        self.org.prepare(self.identity());self.wait('reviewing')
        r=next(iter(self.org.plan['rows'].values()));p=self.dest/r['target'];p.parent.mkdir(parents=True);p.write_bytes(b'new external content')
        with self.assertRaisesRegex(ValueError,'冲突'):app.copy_one(r,self.src,self.dest,exact=True)
        self.assertEqual(p.read_bytes(),b'new external content')
    def test_excluded_files_not_copied_and_repeat_duplicate(self):
        sid=self.org.plan['groups'][0]['shots'][0]
        self.org.edit(dict(self.identity(),action='exclude',shots=[sid]))
        self.org.prepare(self.identity());self.wait('reviewing');self.org.start_copy(self.identity());s=self.wait('done')
        self.assertEqual(len(s['results']),6)
        self.org.state['phase']='ready';self.org.prepare(self.identity());s=self.wait('reviewing')
        self.assertEqual(s['preflight']['duplicates'],6)
    def test_destination_symlink_rejected_and_no_writes(self):
        outside=self.root/'elsewhere';outside.mkdir();self.dest.symlink_to(outside,target_is_directory=True)
        self.org._prepare();self.assertTrue(self.org.state.get('error'));self.assertFalse(list(outside.iterdir()))

if __name__=='__main__':unittest.main()
