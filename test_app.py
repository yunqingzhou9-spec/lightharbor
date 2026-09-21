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
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.src = self.root / 'card'; self.src.mkdir()
        self.dest = self.root / 'archive'; self.dest.mkdir()
    def tearDown(self): self.tmp.cleanup()
    def fixture(self, name='DCIM/item.txt', data=b'original'):
        p = self.src / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data); return p
    def plan(self, options=None): return app.scan(self.src, self.dest, options or {'date': True, 'type': True})[0]
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
    def test_metadata_and_scene_uncertainty(self):
        self.fixture('image.jpg')
        meta={'date':'2020:05:06 12:30:40','gps':[-33.86,151.21],'labels':[{'label':'beach','score':.9}]}
        with patch.object(app.Probe, 'read', return_value=meta):
            row = self.plan(dict(date=True,type=True,location=True,scene=True))[0]
        self.assertTrue(row['target'].startswith('2020/2020-05-06/照片/GPS_-33.86_+151.20/海滩与海洋/'))
        self.assertEqual(row['date_source'],'拍摄日期')
        self.assertEqual(app.scene_label([{'label':'beach','score':.2}])[0], '景色待确认')
        self.assertEqual(app.scene_label([{'label':'beach','score':.8},{'label':'mountain','score':.78}])[0], '景色待确认')
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
    def test_unknown_metadata_explicit(self):
        self.fixture('broken.heic',b'not an image')
        row=self.plan({'date':True,'type':True,'location':True,'scene':True})[0]
        self.assertEqual(row['date_source'],'文件修改日期')
        self.assertEqual(row['location'],'位置未知')
        self.assertEqual(row['scene'],'景色待确认')
        self.assertTrue(row['warning'])

if __name__=='__main__':unittest.main()
