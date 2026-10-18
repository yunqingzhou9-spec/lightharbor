import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import app
import planner
from test_planner import fixtures
from card_cleanup import card_guard

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
        result=self.org.cleanup_page({});self.assertEqual(result['deleted'],9);self.assertTrue(Path(result['report']).is_file());self.assertIsNone(self.org.plan)
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
    def test_guard_rejects_local_folder(self):
        with self.assertRaises(ValueError):card_guard(self.source,self.backup)
    def test_missing_linked_backup_is_retained(self):
        next(self.backup.iterdir()).unlink();p=self.preview();self.assertEqual(p['files'],6)
        page=self.org.cleanup_page({'offset':200});self.assertEqual(page['rows'],[])
        with self.assertRaises(ValueError):self.org.cleanup_page({'offset':-1})
