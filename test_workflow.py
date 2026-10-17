import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import app
import planner
from test_planner import fixtures


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name).resolve()
        self.src=self.root/'card'; self.src.mkdir(); self.dest=self.root/'archive'; self.store=self.root/'tasks'
        rows=fixtures(n=3,paired=True)
        for i,r in enumerate(rows):
            p=self.src/r['relative'];p.parent.mkdir(exist_ok=True);p.write_bytes(f'content-{i}'.encode())
            r.update(source=str(p),size=p.stat().st_size,fingerprint=app.fingerprint(p))
        self.org=app.Organizer(self.store);self.org.rows=rows;self.org.source=self.src;self.org.destination=self.dest
        self.org.plan=planner.build_plan(rows,{'min_frames':3,'duplicate_mode':'content'});self.org.revision=1;self.org.state.update(phase='ready');self.org._publish();self.org.save_task()
    def test_folder_toggle_and_restart(self):
        org=self.org;folder=org.plan['groups'][0]['folder']
        self.assertEqual(org.snapshot()['included'],9)
        org.set_scope({**self.identity(),'mode':'toggle_folder','folder':folder,'checked':False})
        self.assertEqual(org.snapshot()['included'],0)
        org.set_scope({**self.identity(),'mode':'toggle_folder','folder':folder.split('/')[0],'checked':True})
        self.assertEqual(org.snapshot()['included'],9)
        sid=next(iter(org.plan['shots']))
        org.set_scope({**self.identity(),'mode':'photos','shots':[sid]})
        self.assertEqual(sum(org.snapshot()['scope_groups'].values()),3)
        org.state['phase']='done'
        org.set_scope({**self.identity(),'mode':'all'})
        self.assertEqual(org.snapshot()['phase'],'ready')
        self.assertEqual(org.snapshot()['included'],9)

    def test_direct_photo_scope_and_legacy_exclusion(self):
        org=self.org;ids=list(org.plan['shots'])
        org.plan['shots'][ids[0]]['excluded']=True
        planner.refresh(org.plan);org._publish()
        org.set_scope({**self.identity(),'mode':'toggle_shots','shots':[ids[1]],'checked':False})
        self.assertEqual(org.active_shots(),{ids[2]})
        self.assertFalse(any(s['excluded'] for s in org.plan['shots'].values()))
        org.set_scope({**self.identity(),'mode':'toggle_shots','shots':[ids[0]],'checked':True})
        self.assertEqual(org.active_shots(),{ids[0],ids[2]})
        self.assertEqual(sum(org.snapshot()['scope_groups'].values()),6)
        org.set_scope({**self.identity(),'mode':'all'})
        self.assertEqual(org.snapshot()['included'],9)
        org.set_scope({**self.identity(),'mode':'toggle_shots','shots':ids,'checked':False})
        self.assertEqual(org.snapshot()['included'],0)
        with self.assertRaises(ValueError):org.prepare(self.identity())

    def tearDown(self): self.tmp.cleanup()
    def identity(self): return {'plan_id':self.org.plan['id'],'revision':self.org.plan['revision']}
    def wait(self,phase):
        for _ in range(1000):
            s=self.org.snapshot()
            if s['phase']==phase:return s
            if s.get('error'):self.fail(s)
            time.sleep(.003)
        self.fail(self.org.snapshot())
    def prepare(self):self.org.prepare(self.identity());return self.wait('reviewing')
    def execute(self,policy=None):self.org.start_copy(dict(self.identity(),duplicate_policy=policy));return self.wait('done')
    def ready(self):self.org.state['phase']='ready'
    def resume(self):
        task=self.org.plan['id']; self.org=app.Organizer(self.store)
        self.org.resume_task({'task':task});return self.wait('ready')
    def test_default_all_and_associated_photo_scope(self):
        self.assertEqual(self.org.state['included'],9)
        sid=next(iter(self.org.plan['shots']))
        old=self.identity();self.org.set_scope(dict(old,mode='photos',shots=[sid]))
        self.assertEqual(self.org.state['included'],3)
        with self.assertRaises(ValueError):self.org.prepare(old)
        self.prepare();s=self.execute();self.assertEqual(s['result_counts']['已复制'],3)
        self.assertEqual({Path(r['source']).suffix for r in s['results']},{'.nef','.jpg','.xmp'})
    def test_folder_scope_parent_and_child_not_double_counted(self):
        folder=self.org.plan['groups'][0]['folder'];parent=folder.split('/')[0]
        self.org.set_scope(dict(self.identity(),mode='folders',folders=[parent,folder]))
        self.assertEqual(self.org.state['included'],9)
        self.org.set_scope(dict(self.identity(),mode='all'));self.assertEqual(self.org.state['included'],9)
        with self.assertRaises(ValueError):self.org.set_scope(dict(self.identity(),mode='folders',folders=['../outside']))
    def test_photo_scope_accumulates_and_survives_rename_and_restart(self):
        sids=list(self.org.plan['shots']);gid=self.org.plan['groups'][0]['id']
        self.org.set_scope(dict(self.identity(),mode='photos',shots=[sids[0]]))
        self.org.set_scope(dict(self.identity(),mode='photos',shots=[sids[1]],append=True))
        self.org.edit(dict(self.identity(),action='rename',group=gid,name='山顶日落'))
        s=self.resume();self.assertEqual(s['included'],6);self.assertEqual(s['groups'][0]['name'],'山顶日落')
        self.assertFalse(self.dest.exists());self.prepare();self.assertFalse(self.dest.exists())
    def test_duplicate_requires_manual_choice_every_time(self):
        self.prepare();self.execute();self.ready();s=self.prepare()
        self.assertEqual(s['preflight']['duplicates'],9)
        with self.assertRaisesRegex(ValueError,'手动'):self.org.start_copy(self.identity())
        s=self.execute('skip');self.assertEqual(s['result_counts']['重复跳过'],9)
        self.ready();self.prepare()
        with self.assertRaisesRegex(ValueError,'手动'):self.org.start_copy(self.identity())
        s=self.execute('again');self.assertEqual(s['result_counts']['已复制'],9)
        self.assertTrue(all('__1.' in r['target'] for r in s['results']))
    def test_interrupted_copy_restart_rechecks_and_resumes(self):
        self.prepare();original=app.copy_one;calls=0
        def stop_after_two(*args,**kw):
            nonlocal calls
            result=original(*args,**kw);calls+=1
            if calls==2:self.org.cancel.set()
            return result
        with patch.object(app,'copy_one',side_effect=stop_after_two):
            self.org.start_copy(self.identity());s=self.wait('cancelled')
        self.assertEqual(s['processed'],2)
        self.resume();s=self.prepare();self.assertEqual(s['preflight']['duplicates'],2)
        with self.assertRaises(ValueError):self.org.start_copy(self.identity())
        s=self.execute('skip');self.assertEqual(s['result_counts'],{'已复制':7,'重复跳过':2,'失败':0})
        for r in s['results']:self.assertEqual(app.digest(r['source']),app.digest(r['target']))
    def test_history_finds_verified_copy_after_plan_folder_changed(self):
        self.prepare();self.execute();self.ready()
        self.org.edit(dict(self.identity(),action='rename',group=self.org.plan['groups'][0]['id'],name='重新命名'))
        s=self.prepare();self.assertEqual(s['preflight']['duplicates'],9)
        page=self.org.duplicate_page(dict(self.identity(),offset=0));self.assertEqual(page['total'],9)
        self.assertIn('重新命名',page['rows'][0]['copy_target'])
        self.assertNotIn('重新命名',page['rows'][0]['existing'])
    def test_changed_duplicate_is_not_skipped(self):
        self.prepare();self.execute();self.ready();self.prepare()
        row=self.org.active_rows()[0];target=Path(row['duplicate_target']);target.write_bytes(b'corrupted')
        s=self.execute('skip');self.assertEqual(s['result_counts']['失败'],1);self.assertEqual(s['result_counts']['重复跳过'],8)
        self.assertEqual(target.read_bytes(),b'corrupted')
    def test_new_identical_conflict_after_confirmation_is_failure_not_auto_skip(self):
        self.prepare();row=self.org.active_rows()[0];target=self.dest/row['target'];target.parent.mkdir(parents=True);target.write_bytes(Path(row['source']).read_bytes())
        s=self.execute();self.assertEqual(s['result_counts']['失败'],1);self.assertEqual(s['result_counts']['重复跳过'],0)
    def test_failed_checkpoint_does_not_leave_copying_phase(self):
        self.prepare()
        with patch.object(self.org,'save_task',side_effect=OSError('disk full')):
            with self.assertRaisesRegex(ValueError,'尚未开始复制'):self.org.start_copy(self.identity())
        self.assertEqual(self.org.state['phase'],'reviewing');self.assertFalse(self.dest.exists())

    def test_missing_or_changed_source_blocks_resume_without_copy(self):
        self.prepare();Path(self.org.active_rows()[0]['source']).write_bytes(b'changed')
        task=self.org.plan['id'];self.org=app.Organizer(self.store);self.org.resume_task({'task':task})
        for _ in range(1000):
            if self.org.state.get('error'):break
            time.sleep(.003)
        self.assertIn('变化',self.org.state['error']);self.assertFalse(self.dest.exists())
    def test_history_outside_destination_and_same_name_different_content_not_duplicates(self):
        self.dest.mkdir();records=self.dest/'.整理记录';records.mkdir()
        row=self.org.active_rows()[0];outside=self.root/'elsewhere.jpg';outside.write_bytes(Path(row['source']).read_bytes())
        (records/'old.jsonl').write_text(json.dumps({'status':'已复制','target':str(outside),'sha256':app.digest(outside)})+'\n')
        target=self.dest/row['target'];target.parent.mkdir(parents=True);target.write_bytes(b'different')
        self.assertEqual(self.prepare()['preflight']['duplicates'],0)
        self.assertEqual(outside.read_bytes(),Path(row['source']).read_bytes())

    def test_import_overview_content_verified_not_filename_only(self):
        self.org.assess_imports()
        self.assertEqual(sum(d['new'] for d in self.org.import_overview()),9)
        row=self.org.active_rows()[0];target=self.dest/row['target'];target.parent.mkdir(parents=True)
        target.write_bytes(b'different content')
        self.org.assess_imports();self.assertEqual(sum(d['check'] for d in self.org.import_overview()),1)
        target.write_bytes(Path(row['source']).read_bytes())
        self.org.assess_imports();self.assertEqual(sum(d['backed_up'] for d in self.org.import_overview()),1)
        detail=self.org.import_details(dict(self.identity(),status='backed_up'))
        self.assertEqual(detail['total'],1);self.assertEqual(detail['rows'][0]['copy'],str(target))
        self.assertFalse((self.dest/'.整理记录').exists())

    def test_associated_files_share_shooting_date_in_overview(self):
        for row in self.org.plan['rows'].values():
            if Path(row['source']).suffix!='.jpg': row['date']='2000-01-01'
        self.org.assess_imports()
        self.assertNotIn('2000-01-01',[d['date'] for d in self.org.import_overview()])
        self.prepare();self.execute()
        self.assertEqual(sum(d['backed_up'] for d in self.org.import_overview()),9)

    def test_pause_resume_and_progress(self):
        self.prepare();original=app.copy_one;first=True
        def pause_first(*args,**kw):
            nonlocal first
            if first:
                first=False;self.org.pause_copy(self.identity())
            return original(*args,**kw)
        with patch.object(app,'copy_one',side_effect=pause_first):
            self.org.start_copy(self.identity());self.wait('paused')
            before=self.org.snapshot()['transfer']['done_bytes'];time.sleep(.03)
            self.assertEqual(self.org.snapshot()['transfer']['done_bytes'],before)
            with self.assertRaises(ValueError):self.org.start_scan({'source':str(self.src),'destination':str(self.dest)})
            self.org.continue_copy(self.identity());s=self.wait('done')
        self.assertEqual(s['result_counts']['已复制'],9)
        self.assertEqual(s['transfer']['done_bytes'],s['transfer']['total_bytes'])
        self.assertGreater(s['transfer']['elapsed'],0)

    def test_cancel_while_paused_finishes_worker(self):
        self.prepare();original=app.copy_one
        def pause(*args,**kw):
            self.org.pause_copy(self.identity());return original(*args,**kw)
        with patch.object(app,'copy_one',side_effect=pause):
            self.org.start_copy(self.identity());self.wait('paused');self.org.cancel.set();s=self.wait('cancelled')
        self.assertEqual(s['processed'],0)

    def test_history_survives_restart_and_folder_access_is_bounded(self):
        self.prepare();self.execute()
        page=self.org.history_page({});self.assertEqual(page['total'],1)
        ident=page['rows'][0]['id'];date=page['rows'][0]['dates'][0]
        self.org=app.Organizer(self.store)
        self.assertEqual(self.org.history_page({'query':date})['total'],1)
        self.assertEqual(self.org.history_page({'query':'1900-01-01'})['total'],0)
        detail=self.org.history_detail({'history_id':ident});self.assertEqual(detail['total'],9)
        folder=detail['folders'][0]['path']
        self.assertEqual(self.org.reveal_folder({'history_id':ident,'folder':folder})['path'],folder)
        with self.assertRaises(ValueError):self.org.reveal_folder({'history_id':ident,'folder':str(self.root)})
        with self.assertRaises(ValueError):self.org.history_detail({'history_id':'../outside'})

    def test_retry_failed_requires_recheck_and_manual_duplicate_choice(self):
        self.prepare();row=self.org.active_rows()[0];p=self.dest/row['target'];p.parent.mkdir(parents=True);p.write_bytes(b'conflict')
        s=self.execute();self.assertEqual(s['result_counts']['失败'],1)
        self.org.retry_failed(self.identity());s=self.wait('reviewing')
        self.assertEqual(s['included'],3);self.assertEqual(s['preflight']['duplicates'],2)
        with self.assertRaises(ValueError):self.org.start_copy(self.identity())
        s=self.execute('skip');self.assertEqual(s['result_counts'],{'已复制':1,'重复跳过':2,'失败':0})
        self.assertEqual(self.org.history_page({})['total'],2)

    def test_name_mode_never_reads_file_content_and_requires_consent(self):
        self.org.plan['options'].pop('duplicate_mode')
        for row in self.org.active_rows():
            target=self.dest/row['target'];target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'different')
        with patch.object(app,'digest',side_effect=AssertionError('name matching must not read file content')):
            self.org.assess_imports();self.assertEqual(sum(d['backed_up'] for d in self.org.import_overview()),9)
            self.prepare()
            with self.assertRaises(ValueError):self.org.start_copy(self.identity())
            s=self.execute('skip')
        self.assertEqual(s['result_counts']['重复跳过'],9)
        self.assertTrue(all(r['content_verified'] is False and 'sha256' not in r for r in s['results']))
        self.assertIn('未核对内容',self.org.active_rows()[0]['import_note'])

    def test_strict_hashes_reused_across_scan_prepare_and_confirm(self):
        for row in self.org.active_rows():
            target=self.dest/row['target'];target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(Path(row['source']).read_bytes())
        with patch.object(app,'digest',wraps=app.digest) as read:
            self.org.assess_imports();initial=read.call_count;self.assertEqual(initial,18)
            self.prepare();self.assertEqual(read.call_count,initial)
            self.execute('skip');self.assertEqual(read.call_count,initial)
            target=Path(self.org.active_rows()[0]['duplicate_target']);target.write_bytes(b'changed')
            self.org.verified_digest(target,lambda:False);self.assertEqual(read.call_count,initial+1)

    def test_name_match_finds_other_folder_and_never_follows_symlinks(self):
        self.org.plan['options']['duplicate_mode']='name'
        row=self.org.active_rows()[0];target=self.dest/'previous'/Path(row['source']).name
        target.parent.mkdir(parents=True);target.write_bytes(b'name only')
        outside=self.root/'outside';outside.mkdir();(outside/'other.nef').write_bytes(b'outside')
        (self.dest/'link').symlink_to(outside,target_is_directory=True)
        self.org.assess_imports();self.assertEqual(row['duplicate_target'],str(target))
        self.prepare();target.write_bytes(b'changed after consent preview')
        s=self.execute('skip');self.assertEqual(s['result_counts']['失败'],1)

    def test_copy_streams_source_without_extra_hash_read(self):
        self.org.plan['options']['duplicate_mode']='name';self.prepare()
        with patch.object(app,'digest',wraps=app.digest) as read:
            s=self.execute()
        self.assertEqual(s['result_counts']['已复制'],9)
        self.assertEqual(read.call_count,9)
        self.assertTrue(all(self.dest in Path(c.args[0]).parents for c in read.call_args_list))

if __name__=='__main__':unittest.main()
