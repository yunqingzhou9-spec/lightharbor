"""Generated fixtures only: never access personal photographs."""
import base64
import hashlib
import io
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
import windows_media
from windows_folder_picker import FolderPickerAPI, PAGE_SIZE


def generated_dng(path, width=128, height=96, orientation=1, embedded=None):
    """Minimal uncompressed Bayer DNG containing generated pixels, no camera photo."""
    from array import array
    def shorts(*values): return struct.pack('<'+'H'*len(values), *values)
    def longs(*values): return struct.pack('<'+'I'*len(values), *values)
    def rationals(*values): return b''.join(struct.pack('<ii', n, d) for n, d in values)
    entries = {
        256:(4,1,longs(width)), 257:(4,1,longs(height)), 258:(3,1,shorts(16)),
        259:(3,1,shorts(1)), 262:(3,1,shorts(32803)),
        271:(2,12,b'LightHarbor\0\0'),272:(2,14,b'Generated DNG\0'),
        273:(4,1,longs(0)), 274:(3,1,shorts(orientation)),277:(3,1,shorts(1)),
        278:(4,1,longs(height)),279:(4,1,longs(width*height*2)),284:(3,1,shorts(1)),
        33421:(3,2,shorts(2,2)),33422:(1,4,bytes([0,1,1,2])),
        50706:(1,4,bytes([1,4,0,0])),50707:(1,4,bytes([1,1,0,0])),
        50708:(2,14,b'Generated DNG\0'),50714:(3,1,shorts(0)),50717:(4,1,longs(4095)),
        50721:(10,9,rationals((1,1),(0,1),(0,1),(0,1),(1,1),(0,1),(0,1),(0,1),(1,1))),
        50728:(5,3,rationals((1,1),(1,1),(1,1))),50778:(3,1,shorts(21))}
    extra = bytearray(); start = 8+2+12*len(entries)+4
    table = []
    for tag,(typ,count,data) in sorted(entries.items()):
        if len(data)>4:
            value=struct.pack('<I',start+len(extra));extra.extend(data)
            if len(extra)%2:extra.append(0)
        else:value=data.ljust(4,b'\0')
        table.append([tag,typ,count,value])
    thumbnail_ifd = start+len(extra) if embedded else 0
    pixels=start+len(extra)+(30 if embedded else 0)
    body=b''.join(struct.pack('<HHI',tag,typ,count)+(longs(pixels) if tag==273 else value) for tag,typ,count,value in table)
    raster=array('H',(400+(x>=width//2)*900+(y>=height//2)*1500 for y in range(height) for x in range(width)))
    import sys
    if sys.byteorder!='little':raster.byteswap()
    ifd1=b''
    if embedded:
        ifd1=shorts(2)+struct.pack('<HHII',513,4,1,pixels+len(raster)*2)+struct.pack('<HHII',514,4,1,len(embedded))+longs(0)
    path.write_bytes(b'II*\0'+longs(8)+shorts(len(table))+body+longs(thumbnail_ifd)+extra+ifd1+raster.tobytes()+(embedded or b''))


def generated_embedded_cr2(path, orientation=None, thumbnail_orientation=None, jpeg=None):
    """Synthetic CR2-header TIFF with a JPEG IFD1; not a representative camera RAW."""
    if jpeg is None:
        output=io.BytesIO();Image.new('RGB',(640,400),(45,104,75)).save(output,format='JPEG');jpeg=output.getvalue()
    ifd0=16;parent=struct.pack('<HHI',274,3,1)+struct.pack('<H',orientation)+b'\0\0' if orientation is not None else b''
    ifd1=ifd0+6+len(parent);child=struct.pack('<HHI',274,3,1)+struct.pack('<H',thumbnail_orientation)+b'\0\0' if thumbnail_orientation is not None else b'';jpeg_at=ifd1+2+len(child)+2*12+4
    path.write_bytes(b'II*\0'+struct.pack('<I',ifd0)+b'CR\x02\0'+struct.pack('<I',0)+
        struct.pack('<H',int(bool(parent)))+parent+struct.pack('<I',ifd1)+struct.pack('<H',2+int(bool(child)))+child+
        struct.pack('<HHII',513,4,1,jpeg_at)+struct.pack('<HHII',514,4,1,len(jpeg))+
        struct.pack('<I',0)+jpeg)


class PickerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='lightharbor-picker-')
        self.root=Path(self.temp.name)
        self.api=FolderPickerAPI(self.root,roots=[{'name':'测试磁盘','path':str(self.root)}])
    def tearDown(self): self.temp.cleanup()
    def test_browse_shows_files_and_only_confirms_directory(self):
        (self.root/'下一层').mkdir();(self.root/'sample.CR2').write_bytes(b'temp')
        (self.root/'sample.xmp').write_text('generated')
        data=self.api.browse();self.assertEqual(data['items'][0]['name'],'下一层')
        raw=next(x for x in data['items'] if x['name']=='sample.CR2');self.assertTrue(raw['photo'])
        self.assertFalse(next(x for x in data['items'] if x['name']=='sample.xmp')['photo'])
        self.assertIn('error',self.api.choose('stale'))
        self.assertTrue(self.api.choose(data['listing'])['ok']);self.assertEqual(self.api._result,str(self.root.resolve()))
    def test_empty_folder_can_be_chosen_and_cancel_returns_empty(self):
        data=self.api.browse();self.assertFalse(data['items']);self.api.cancel()
        self.assertEqual(self.api._result,'');self.assertIn('error',self.api.choose(data['listing']))
    def test_invalid_path_and_file_path_rejected(self):
        (self.root/'file').write_bytes(b'x')
        self.assertIn('error',self.api.browse(str(self.root/'missing')))
        self.assertIn('error',self.api.browse(str(self.root/'file')))
    def test_paging_stale_and_no_arbitrary_preview(self):
        for n in range(PAGE_SIZE+7):(self.root/f'{n:03}.jpg').write_bytes(b'generated-invalid')
        first=self.api.browse(request=1);self.assertEqual(len(first['items']),PAGE_SIZE)
        second=self.api.browse(offset=PAGE_SIZE,request=2);self.assertEqual(len(second['items']),7)
        self.assertFalse(self.api.preview(first['listing'],first['items'][0]['id'])['thumbnail'])
        self.assertFalse(self.api.preview(second['listing'],'../../secret')['thumbnail'])
        self.assertIn('error',self.api.browse(request=1));self.assertEqual(self.api._listing,second['listing'])
    def test_thumbnail_cache_and_file_changes(self):
        file=self.root/'sample.jpg';Image.new('RGB',(800,600),'green').save(file)
        data=self.api.browse();key=data['items'][0]['id']
        with patch.object(windows_media,'thumbnail',wraps=windows_media.thumbnail) as thumb:
            a=self.api.preview(data['listing'],key);b=self.api.preview(data['listing'],key)
            self.assertEqual(a,b);self.assertEqual(thumb.call_count,1)
            Image.new('RGB',(600,800),'blue').save(file)
            c=self.api.preview(data['listing'],key);self.assertEqual(thumb.call_count,2)
            self.assertEqual((c['width'],c['height']),(360,480))
    def test_escaped_names_are_data_and_symlink_skipped(self):
        (self.root/'<img onerror=alert(1)>.xmp').write_bytes(b'temporary')
        (self.root/'link').symlink_to(self.root,target_is_directory=True)
        data=self.api.browse();self.assertEqual(len(data['items']),1)
        self.assertEqual(data['items'][0]['name'],'<img onerror=alert(1)>.xmp')


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='lightharbor-codec-');self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def test_common_formats_and_original_bytes_preserved(self):
        for ext in ('jpg','png','tiff','webp','bmp','gif','avif'):
            with self.subTest(ext=ext):
                file=self.root/('generated.'+ext);Image.new('RGB',(800,400),'green').save(file)
                before=file.read_bytes();result=windows_media.thumbnail(file)
                self.assertEqual((result['width'],result['height']),(480,240));self.assertEqual(file.read_bytes(),before)
    def test_heif(self):
        from pillow_heif import register_heif_opener
        register_heif_opener();file=self.root/'generated.heic';Image.new('RGB',(320,240),'blue').save(file)
        self.assertEqual(windows_media.thumbnail(file)['width'],320)
    def test_generated_embedded_cr2(self):
        file=self.root/'generated.cr2';generated_embedded_cr2(file);before=file.read_bytes()
        result=windows_media.thumbnail(file,raw=True)
        self.assertTrue(result['thumbnail']);self.assertEqual(result['source'],'RAW 内嵌预览')
        self.assertEqual((result['width'],result['height']),(480,300));self.assertEqual(file.read_bytes(),before)
    def test_generated_dng_native_decode_subprocess(self):
        file=self.root/'generated.dng';generated_dng(file);before=file.read_bytes()
        result=windows_media.thumbnail(file,raw=True)
        self.assertTrue(result['thumbnail'],result);self.assertEqual(result['source'],'RAW 解码预览')
        self.assertEqual(file.read_bytes(),before)
    def test_invalid_raw_and_timeout_are_safe_placeholders(self):
        import subprocess
        file=self.root/'invalid.cr2';file.write_bytes(b'not a camera photo')
        self.assertFalse(windows_media.thumbnail(file,raw=True)['thumbnail'])
        with patch('windows_media.subprocess.run',side_effect=subprocess.TimeoutExpired('decoder',25)):
            self.assertFalse(windows_media._raw_preview(file,'low')['thumbnail'])
        self.assertEqual(file.read_bytes(),b'not a camera photo')
    def test_orientation_and_quality(self):
        file=self.root/'oriented.jpg';exif=Image.Exif();exif[274]=6
        Image.new('RGB',(800,400),'green').save(file,exif=exif)
        self.assertEqual(windows_media.thumbnail(file)['height'],480)
        self.assertEqual(windows_media.thumbnail(file,'high')['height'],800)
        with self.assertRaises(ValueError):windows_media.thumbnail(file,'invalid')


if __name__=='__main__':unittest.main()
