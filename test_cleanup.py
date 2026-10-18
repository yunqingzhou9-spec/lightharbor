import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import app
import planner
from test_planner import fixtures
from card_cleanup import card_guard, source_card_guard

class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()
        self.source=self.root/'card';self.source.mkdir();self.backup=self.root/'backup';self.backup.mkdir()
        self.org=app.Organizer(self.root/'tasks');self.org.source=self.source;self.org.destination=self.backup
        rows=fixtures(n=3,paired=True)
        for i,row in enumerate(rows):
            row['relative']=Path(row['relative']).name;p=self.source/row['relative'];p.write_bytes(('photo'+str(i)).encode());(self.backup/p.name).write_bytes(p.read_bytes())
            row.update(source=str(p),size=p.stat().st_size,fingerprint=app.fingerprint(p))
        self.org.rows=rows;self.org.plan=planner.build_plan(rows,{'min_frames':3});self.org.revision=1;self.org.state['phase']='ready';self.org._publish()
        self.guard=patch('card_cleanup.card_guard',return_value=(self.source,self.backup));self.guard.start()
        self.vol=patch('card_cleanup.off_volume',return_value=True);self.vol.start()
    def tearDown(self):self.vol.stop();self.guard.stop();self.tmp.cleanup()
    def ident(self):return {'plan_id':self.org.plan['id'],'revision':self.org.revision}
    def wait(self):
        for _ in range(1000):
            if self.org.state['phase']!='preparing':return
            time.sleep(.002)
        self.fail('timeout')
    def preview(self):
        date=next(iter(self.org.plan['shots'].values()))['date']
        self.org.cleanup_check({**self.ident(),'dates':[date]});self.wait();return self.org.cleanup_page({})
    def confirm(self,p):
        self.org.cleanup_execute({**self.ident(),'token':p['token'],'confirmation':f"删除 {p['files']} 个文件"});self.wait()
    def test_preview_is_readonly_and_confirmation_deletes_only_sources(self):
        p=self.preview();self.assertEqual(p['files'],9);self.assertEqual(len(list(self.source.iterdir())),9)
        with self.assertRaises(ValueError):self.org.cleanup_execute({**self.ident(),'token':p['token']})
        self.confirm(p);self.assertEqual(len(list(self.source.iterdir())),0);self.assertEqual(len(list(self.backup.iterdir())),9)
        result=self.org.cleanup_page({});self.assertEqual(result['deleted'],9);self.assertTrue(Path(result['report']).is_file());self.assertEqual(len(self.org.plan['rows']),0);self.assertTrue(self.org.plan['options']['cleanup_only'])
    def test_one_unbacked_sidecar_keeps_its_entire_shot(self):
        xmp=next(self.backup.glob('*.xmp'));xmp.write_bytes(b'not the same')
        p=self.preview();self.assertEqual(p['blocked'],3);self.assertEqual(p['files'],6)
        self.confirm(p);self.assertEqual(len(list(self.source.iterdir())),3)
    def test_backup_changed_after_preview_aborts_whole_batch(self):
        p=self.preview();next(self.backup.iterdir()).write_bytes(b'changed')
        self.confirm(p);self.assertEqual(len(list(self.source.iterdir())),9);self.assertEqual(self.org.cleanup['phase'],'error')
    def test_source_changed_after_preview_aborts_whole_batch(self):
        p=self.preview();next(self.source.iterdir()).write_bytes(b'changed')
        self.confirm(p);self.assertEqual(len(list(self.source.iterdir())),9)
    def test_stale_revision_expiry_and_wrong_token(self):
        p=self.preview()
        for token,confirmation in [('wrong',f"删除 {p['files']} 个文件"),(p['token'],'删除 1 个文件')]:
            with self.assertRaises(ValueError):self.org.cleanup_execute({**self.ident(),'token':token,'confirmation':confirmation})
        self.org.cleanup['expires']=0
        with self.assertRaises(ValueError):self.confirm(p)
        p=self.preview();self.org.set_scope({**self.ident(),'mode':'all'})
        with self.assertRaises(ValueError):self.confirm(p)
        self.assertEqual(len(list(self.source.iterdir())),9)
    def test_matching_name_without_matching_content_is_never_eligible(self):
        for p in self.backup.iterdir():p.write_bytes(b'wrong!')
        p=self.preview();self.assertEqual(p['files'],0);self.assertEqual(p['blocked'],9)
    def test_cancelled_execution_does_not_delete(self):
        p=self.preview();self.org.cleanup['phase']='deleting';self.org.cancel.set();self.org._cleanup_execute()
        self.assertEqual(len(list(self.source.iterdir())),9)
    def test_verified_guard_rejects_same_volume_backup(self):
        with self.assertRaisesRegex(ValueError,'另一个磁盘卷'):card_guard(self.source,self.backup)
    def test_missing_linked_backup_is_retained(self):
        next(self.backup.iterdir()).unlink();p=self.preview();self.assertEqual(p['files'],6)
        page=self.org.cleanup_page({'offset':200});self.assertEqual(page['rows'],[])
        with self.assertRaises(ValueError):self.org.cleanup_page({'offset':-1})

class DirectCleanupTests(CleanupTests):
    def direct_preview(self):
        date=next(iter(self.org.plan['shots'].values()))['date']
        with patch('card_cleanup.source_card_guard',return_value=self.source), patch.object(self.org,'name_candidates',side_effect=AssertionError('must not search backups')), patch.object(self.org,'history_candidates',side_effect=AssertionError('must not read history')), patch('app.digest',side_effect=AssertionError('must not hash')):
            self.org.cleanup_check({**self.ident(),'dates':[date],'mode':'direct'});self.wait()
        return self.org.cleanup_page({})
    def test_direct_does_not_require_backup_or_hash(self):
        for p in self.backup.iterdir():p.unlink()
        self.backup.rmdir()
        p=self.direct_preview();self.assertEqual(p['files'],9);self.assertEqual(p['mode'],'direct')
        with patch('card_cleanup.source_card_guard',return_value=self.source),patch('app.digest',side_effect=AssertionError('must not hash')):
            self.confirm(p)
        self.assertEqual(len(list(self.source.iterdir())),0);self.assertEqual(self.org.cleanup['phase'],'finished')
    def test_direct_changed_source_stops_whole_batch(self):
        p=self.direct_preview();next(self.source.iterdir()).write_bytes(b'changed')
        with patch('card_cleanup.source_card_guard',return_value=self.source):self.confirm(p)
        self.assertEqual(len(list(self.source.iterdir())),9)
    def test_direct_requires_confirmation(self):
        p=self.direct_preview()
        with patch('card_cleanup.source_card_guard',return_value=self.source):
            with self.assertRaises(ValueError):self.org.cleanup_execute({**self.ident(),'token':p['token']})
        self.assertEqual(len(list(self.source.iterdir())),9)
    def test_cleanup_plan_cannot_copy(self):
        self.org.plan['options']['cleanup_only']=True
        with self.assertRaisesRegex(ValueError,'清理扫描'):self.org.prepare(self.ident())
    def test_direct_scan_needs_no_destination_and_skips_backup_assessment(self):
        rows=self.org.rows[:]
        with patch('card_cleanup.source_card_guard',return_value=self.source),patch('app.scan',return_value=(rows,[])),patch.object(self.org,'assess_imports',side_effect=AssertionError('backup check forbidden')):
            self.org.cleanup_scan({'source':str(self.source)})
            for _ in range(1000):
                if self.org.state['phase']!='scanning':break
                time.sleep(.002)
        self.assertEqual(self.org.state['phase'],'ready')
        self.assertTrue(self.org.plan['options']['cleanup_only'])

class LocalFolderCleanupTests(DirectCleanupTests):
    def test_local_folder_preview_and_confirm_with_real_guard(self):
        date=next(iter(self.org.plan['shots'].values()))['date']
        self.org.cleanup_check({**self.ident(),'dates':[date],'mode':'direct'})
        self.wait();p=self.org.cleanup_page({})
        self.assertEqual(p['files'],9)
        self.assertEqual(len(list(self.source.iterdir())),9)
        self.confirm(p)
        self.assertEqual(self.org.cleanup['phase'],'finished')
        self.assertEqual(len(list(self.source.iterdir())),0)
        self.assertEqual(len(list(self.backup.iterdir())),9)
    def test_empty_missing_and_file_paths_rejected(self):
        for path in ['',self.root/'missing',next(self.source.iterdir())]:
            with self.assertRaises(ValueError):source_card_guard(path)
    def test_cleanup_scan_allows_folder_containing_task_store(self):
        self.org.store=self.source/'tasks'
        with patch('app.scan',return_value=(self.org.rows[:],[])):
            self.org.cleanup_scan({'source':str(self.source)})
            for _ in range(1000):
                if self.org.state['phase']!='scanning':break
                time.sleep(.002)
        self.assertEqual(self.org.state['phase'],'ready')

class SingleConfirmationTests(CleanupTests):
    def test_explicit_click_confirmation_and_preview_metadata(self):
        date=next(iter(self.org.plan['shots'].values()))['date']
        self.org.cleanup_check({**self.ident(),'dates':[date],'mode':'direct'});self.wait()
        p=self.org.cleanup_page({})
        self.assertTrue(all(row['preview_file'] in self.org.plan['rows'] for row in p['rows']))
        for payload in [{'confirmed':False,'count':9},{'confirmed':True,'count':8},{'confirmed':True,'count':True}]:
            with self.assertRaises(ValueError):self.org.cleanup_execute({**self.ident(),'token':p['token'],**payload})
        self.assertEqual(len(list(self.source.iterdir())),9)
        self.org.cleanup_execute({**self.ident(),'token':p['token'],'confirmed':True,'count':9});self.wait()
        self.assertEqual(self.org.cleanup['phase'],'finished')
        self.assertEqual(len(list(self.backup.iterdir())),9)

class SelectedPhotoCleanupTests(CleanupTests):
    def test_catalog_is_readonly_and_contains_associated_files(self):
        catalog=self.org.cleanup_dates(self.ident())
        self.assertEqual(len(catalog['shots']),3)
        self.assertEqual(sum(s['count'] for s in catalog['shots']),9)
        self.assertEqual(len(list(self.source.iterdir())),9)
    def test_selected_shot_deletes_only_its_associated_files(self):
        shots=list(self.org.plan['shots'].values());chosen=shots[1]
        self.org.cleanup_check({**self.ident(),'mode':'direct','dates':[chosen['date']],'shots':[chosen['id']]});self.wait()
        p=self.org.cleanup_page({});self.assertEqual(p['files'],3)
        self.assertTrue(all(row['shot']==chosen['id'] for row in p['rows']))
        self.confirm(p)
        self.assertEqual(len(list(self.source.iterdir())),6)
        self.assertEqual(len(list(self.backup.iterdir())),9)
    def test_invalid_or_empty_selection_is_rejected(self):
        date=next(iter(self.org.plan['shots'].values()))['date']
        for selected in [[],['missing'],'all',[None]]:
            with self.assertRaises(ValueError):self.org.cleanup_check({**self.ident(),'mode':'direct','dates':[date],'shots':selected})
        self.assertEqual(len(list(self.source.iterdir())),9)

    def test_preferences_survive_restart(self):
        values={'mode':'legacy','detect':False,'min_frames':45,'tolerance':15}
        self.org.preferences({'values':values})
        fresh=app.Organizer(self.org.store)
        self.assertEqual(fresh.preferences({}),values)

class ContinueCleanupTests(CleanupTests):
    def delete_one(self):
        shot=next(iter(self.org.plan['shots'].values()))
        self.org.cleanup_check({**self.ident(),'mode':'direct','dates':[shot['date']],'shots':[shot['id']]});self.wait()
        p=self.org.cleanup_page({});self.assertEqual(p['files'],3)
        old=self.ident();self.confirm(p)
        self.assertEqual(self.org.state['phase'],'ready')
        with self.assertRaises(ValueError):self.org.cleanup_execute({**old,'token':p['token'],'confirmed':True,'count':3})
    def test_three_consecutive_deletions_without_rescan(self):
        for remaining in [6,3,0]:
            self.delete_one()
            self.assertEqual(len(self.org.rows),remaining)
            self.assertEqual(len(list(self.source.iterdir())),remaining)
            catalog=self.org.cleanup_dates(self.ident())
            self.assertEqual(sum(s['count'] for s in catalog['shots']),remaining)
        self.assertEqual(len(list(self.backup.iterdir())),9)
    def test_changed_remaining_photo_still_cannot_be_deleted(self):
        self.delete_one()
        for p in self.source.iterdir():p.write_bytes(b'changed after scan')
        shot=next(iter(self.org.plan['shots'].values()))
        self.org.cleanup_check({**self.ident(),'mode':'direct','dates':[shot['date']]});self.wait()
        self.assertEqual(self.org.cleanup_page({})['files'],0)
        self.assertEqual(len(list(self.source.iterdir())),6)
