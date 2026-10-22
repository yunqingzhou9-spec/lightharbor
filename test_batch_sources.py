"""Real copy checks using generated temporary bytes only."""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import app
import planner
from windows_folder_picker import FolderPickerAPI


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='lightharbor-opt043-test-')
        self.root=Path(self.temp.name).resolve()
        self.a=self.root/'camera-a';self.b=self.root/'camera-b';self.dest=self.root/'archive'
        self.a.mkdir();self.b.mkdir()
        self.org=app.Organizer(self.root/'state'/'tasks')
        self.probe=patch.object(app.Probe,'read',side_effect=lambda p,_: {'date':f'2026:10:22 12:00:{int("".join(c for c in p.stem if c.isdigit()) or "0")%60:02d}','camera':'generated'})
        self.probe.start()
    def tearDown(self):
        self.org.cancel.set()
        self.probe.stop();self.temp.cleanup()
    def file(self,root,name,content=None):
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(content or ('generated-'+name).encode());return p
    def wait(self):
        for _ in range(1000):
            if self.org.state['phase'] not in ('scanning','preparing','copying','paused'):return self.org.snapshot()
            time.sleep(.005)
        self.fail('temporary test workflow timed out')
    def scan(self,sources=None,**opts):
        self.org.start_scan({'sources':[str(p) for p in (sources or [self.a])],'destination':str(self.dest),'options':{'detect':False,**opts}})
        state=self.wait();self.assertEqual(state['phase'],'ready',state.get('error'));return state
    def ident(self):return {'plan_id':self.org.plan['id'],'revision':self.org.plan['revision']}
    def choose(self,ids):self.org.set_scope({**self.ident(),'mode':'toggle_shots','shots':list(self.org.plan['shots']),'checked':False});self.org.set_scope({**self.ident(),'mode':'toggle_shots','shots':list(ids),'checked':True})
    def prepare(self):
        self.org.prepare(self.ident());state=self.wait();self.assertEqual(state['phase'],'reviewing',state.get('error'));return state
    def execute(self,policy='again'):
        self.org.start_copy({**self.ident(),'duplicate_policy':policy});state=self.wait();self.assertEqual(state['phase'],'done',state.get('error'));return state
    def test_multisource_pairing_and_overlap(self):
        self.file(self.a,'same.jpg');self.file(self.b,'same.nef');self.file(self.b,'same.xmp')
        self.scan([self.a,self.b])
        self.assertEqual(len(self.org.plan['shots']),2)
        self.assertEqual(sorted(len(s['rows']) for s in self.org.plan['shots'].values()),[1,2])
        sub=self.a/'sub';sub.mkdir();self.file(sub,'another.jpg')
        self.scan([self.a,sub]);self.assertEqual(len(self.org.rows),2);self.assertEqual(self.org.sources,[self.a])
    def test_invalid_source_blocks_scan(self):
        with self.assertRaises((ValueError,OSError)):self.scan([self.a,self.root/'missing'])
        self.dest.mkdir()
        with self.assertRaises(ValueError):self.scan([self.a,self.dest])
        self.assertIsNone(self.org.plan)
    def test_video_and_format_layout_matrix(self):
        self.file(self.a,'a.jpg');self.file(self.a,'a.nef');self.file(self.a,'a.xmp');self.file(self.a,'clip.mp4')
        self.scan(separate_formats=False)
        rows=list(self.org.plan['rows'].values())
        self.assertEqual(next(r['target'] for r in rows if r['kind']=='视频'),'2026/2026-10-22/视频/clip.mp4')
        self.assertTrue(all('普通照片/' in r['target'] and '/JPEG/' not in r['target'] and '/RAW/' not in r['target'] for r in rows if r['kind']!='视频'))
        self.prepare();state=self.execute();self.assertEqual(state['result_counts']['已复制'],4)
        self.assertTrue(all(Path(r['target']).read_bytes()==Path(r['source']).read_bytes() for r in state['results']))
    def test_only_video_omits_category_and_no_double_video(self):
        self.file(self.a,'clip.mov');self.scan();row=next(iter(self.org.plan['rows'].values()))
        self.assertEqual(row['target'],'2026/2026-10-22/clip.mov')
    def test_single_photo_type_complete_scan_not_selected_subset(self):
        self.file(self.a,'a.jpg');self.file(self.a,'a.nef');self.file(self.a,'video.mp4')
        self.scan();sid=next(sid for sid,s in self.org.plan['shots'].items() if s['photo'])
        self.choose([sid]);self.prepare()
        self.assertTrue(all('/普通照片/' in r['target'] for r in self.org.active_rows()))
    def test_single_photo_formats_toggle_and_preferences(self):
        self.file(self.a,'a.jpg');self.file(self.a,'a.nef');self.scan()
        self.assertEqual({r['target'] for r in self.org.plan['rows'].values()},{'2026/2026-10-22/JPEG/a.jpg','2026/2026-10-22/RAW/a.nef'})
        self.org.preferences({'values':{'separate_formats':False}});self.assertFalse(self.org.preferences({})['separate_formats'])
        self.scan(separate_formats=False);self.assertEqual({str(Path(r['target']).parent) for r in self.org.plan['rows'].values()},{'2026/2026-10-22'})
    def test_batches_history_reselection_and_verified_skip(self):
        original=self.file(self.a,'a.jpg',b'original');self.file(self.a,'b.jpg')
        self.scan();ids=list(self.org.plan['shots']);self.choose(ids[:1]);self.prepare();first=self.execute()
        first_target=first['results'][0]['target'];first_id=self.org.state['execution_id']
        self.org.next_batch(self.ident());self.assertEqual(self.org.snapshot()['included'],0)
        self.choose(ids[:1]);p=self.prepare();self.assertEqual(p['preflight']['duplicates'],1)
        with self.assertRaises(ValueError):self.org.start_copy(self.ident())
        second=self.execute('skip');self.assertEqual(second['result_counts']['重复跳过'],1)
        self.assertEqual(first_target,second['results'][0]['target']);self.assertEqual(original.read_bytes(),b'original')
        self.assertEqual(self.org.history_page({})['total'],2);self.assertEqual(self.org.history_record(first_id)['counts']['已复制'],1)
        self.org.next_batch(self.ident());self.choose(ids[:1]);self.prepare();third=self.execute('again');self.assertNotEqual(third['results'][0]['target'],first_target)
        self.assertEqual(Path(first_target).read_bytes(),b'original')
    def test_same_name_different_content_safely_numbers_pair(self):
        for root,text in [(self.a,b'A'),(self.b,b'B')]:
            for ext in ('jpg','nef','xmp'):self.file(root,'same.'+ext,text+ext.encode())
        self.scan([self.a,self.b],separate_formats=False);self.prepare();state=self.execute();self.assertEqual(state['result_counts']['已复制'],6)
        for r in state['results']:self.assertEqual(Path(r['target']).read_bytes(),Path(r['source']).read_bytes())
        self.assertEqual(len({r['target'] for r in state['results']}),6)
        self.org.next_batch(self.ident());self.org.set_scope({**self.ident(),'mode':'all'});p=self.prepare();self.assertEqual(p['preflight']['duplicates'],6)
    def test_resume_multisource_and_legacy_plan(self):
        self.file(self.a,'a.jpg');self.file(self.b,'b.jpg');self.scan([self.a,self.b]);task=self.org.plan['id']
        self.org=app.Organizer(self.root/'state'/'tasks');self.org.resume_task({'task':task});self.wait();self.assertEqual(len(self.org.copy_roots()),2)
        self.prepare();self.assertEqual(self.execute()['result_counts']['已复制'],2)
    def test_rescan_preserves_only_unchanged_choice_and_classification(self):
        self.file(self.a,'a.jpg');self.scan();sid=next(iter(self.org.plan['shots']))
        self.org.edit({**self.ident(),'action':'photo_kind','shot':sid,'kind':'timelapse'})
        self.file(self.b,'b.jpg');self.scan([self.a,self.b]);self.assertEqual(self.org.active_shots(),{sid})
        self.assertEqual(next(g['kind'] for g in self.org.plan['groups'] if sid in g['shots']),'timelapse')
    def test_timelapse_subsets_and_rescan_keep_directory(self):
        for i in range(6):self.file(self.a,f'img{i}.jpg')
        self.scan(detect=True,min_frames=3);ids=list(self.org.plan['shots']);self.choose(ids[:2]);self.prepare();a=self.execute();folder=Path(a['results'][0]['target']).parent
        self.org.next_batch(self.ident());self.choose(ids[2:]);self.prepare();b=self.execute();self.assertEqual({Path(r['target']).parent for r in b['results']},{folder})
        self.scan(detect=True,min_frames=3);self.org.set_scope({**self.ident(),'mode':'all'});self.prepare();self.assertEqual({Path(r['target']).parent for r in self.org.active_rows()},{folder.relative_to(self.dest)})
    def test_external_duplicate_change_not_silently_skipped(self):
        self.file(self.a,'a.jpg',b'abc');self.scan();self.prepare();first=self.execute();target=Path(first['results'][0]['target'])
        self.org.next_batch(self.ident());self.org.set_scope({**self.ident(),'mode':'all'});self.prepare();target.write_bytes(b'xyz');s=self.execute('skip')
        self.assertEqual(s['result_counts']['失败'],1);self.assertEqual(target.read_bytes(),b'xyz')
    def test_layout_record_symlink_rejected(self):
        self.file(self.a,'a.jpg');self.dest.mkdir();(self.dest/'.整理记录').symlink_to(self.b,target_is_directory=True)
        self.org.start_scan({'source':str(self.a),'destination':str(self.dest)});state=self.wait();self.assertEqual(state['phase'],'error')
        self.assertFalse(list(self.b.iterdir()))
    def test_same_batch_equal_content_requires_decision_and_skips_verified_copy(self):
        self.file(self.a,'same.jpg',b'equal');self.file(self.b,'same.jpg',b'equal')
        self.scan([self.a,self.b]);p=self.prepare();self.assertEqual(p['preflight']['duplicates'],1)
        with self.assertRaises(ValueError):self.org.start_copy(self.ident())
        s=self.execute('skip');self.assertEqual(s['result_counts'],{'已复制':1,'重复跳过':1,'失败':0})
        self.assertEqual(len({r['target'] for r in s['results']}),1)
    def test_existing_omitted_layout_kept_when_new_type_arrives(self):
        self.file(self.a,'a.jpg');self.scan(separate_formats=False);self.prepare();first=self.execute();target=Path(first['results'][0]['target'])
        self.file(self.a,'video.mp4');self.scan(separate_formats=False)
        rows=list(self.org.plan['rows'].values())
        self.assertEqual(self.dest/next(r['target'] for r in rows if r['kind']=='照片'),target)
        self.assertEqual(next(r['target'] for r in rows if r['kind']=='视频'),'2026/2026-10-22/视频/video.mp4')
        self.assertTrue(target.exists())
    def test_new_earlier_and_later_frames_extend_one_known_sequence(self):
        for i in range(3,9):self.file(self.a,f'img{i}.jpg')
        self.scan(detect=True,min_frames=3,separate_formats=False);self.prepare();first=self.execute();folder=Path(first['results'][0]['target']).parent
        for i in range(3):self.file(self.a,f'img{i}.jpg')
        self.scan(detect=True,min_frames=3,separate_formats=False);self.org.set_scope({**self.ident(),'mode':'all'});self.prepare();second=self.execute('skip')
        self.assertEqual({Path(r['target']).parent for r in second['results']},{folder})
        for i in (0,1):(self.a/f'img{i}.jpg').unlink()
        for i in (9,10):self.file(self.a,f'img{i}.jpg')
        # A fresh session has no in-memory reconciliation: archive members still
        # provide one unambiguous identity for all continuously detected frames.
        self.org=app.Organizer(self.root/'state'/'tasks')
        self.scan(detect=True,min_frames=3,separate_formats=False);self.prepare();third=self.execute('skip')
        self.assertEqual({Path(r['target']).parent for r in third['results']},{folder})
    def test_distinct_manual_sequences_stay_separate_after_rescan(self):
        for i in range(6):self.file(self.a,f'img{i}.jpg')
        self.scan(detect=False,separate_formats=False);ids=list(self.org.plan['shots'])
        for selected in (ids[:3],ids[3:]):
            self.choose(selected)
            self.org.edit({**self.ident(),'action':'photo_kind','kind':'timelapse','filter':{}})
        groups=[g for g in self.org.plan['groups'] if g['kind']=='timelapse']
        self.assertEqual(len({g['sequence_key'] for g in groups}),2)
        self.org.set_scope({**self.ident(),'mode':'all'});self.prepare();first=self.execute();mapping={r['source']:str(Path(r['target']).parent) for r in first['results']}
        self.assertEqual(len(set(mapping.values())),2)
        self.org=app.Organizer(self.root/'state'/'tasks');self.scan(detect=True,min_frames=3,separate_formats=False);self.prepare();second=self.execute('skip')
        self.assertEqual({r['source']:str(Path(r['target']).parent) for r in second['results']},mapping)
    def test_stopped_empty_execution_retained_and_next_clears(self):
        self.file(self.a,'a.jpg');self.scan();self.org.state.update(phase='cancelled',execution_id='a'*16,results=[]);self.org.save_execution();self.assertEqual(self.org.history_page({})['total'],1)
        self.org.next_batch(self.ident());self.assertEqual(self.org.state['phase'],'ready');self.assertFalse(self.org.active_rows())


class MultiplePickerTests(unittest.TestCase):
    def test_selection_survives_navigation_and_validates_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();(root/'a').mkdir();(root/'b').mkdir();api=FolderPickerAPI(root,roots=[],multiple=True)
            first=api.browse();self.assertIn('error',api.choose(first['listing']))
            self.assertEqual(api.toggle(first['listing'],str(root/'a'))['selected'],[str(root/'a')])
            second=api.browse(root/'b',request=2);api.toggle(second['listing'],str(root/'b'))
            self.assertIn('error',api.toggle(first['listing'],str(root)))
            self.assertTrue(api.choose(second['listing'])['ok']);self.assertEqual(api._result,[str(root/'a'),str(root/'b')])


if __name__=='__main__':unittest.main()
