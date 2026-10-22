"""Preview regressions using generated pixels and TIFF containers, never camera files."""
import base64
import hashlib
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageStat
import windows_media as media
from test_windows_picker import generated_dng, generated_embedded_cr2


TRANSFORMS = {2: Image.Transpose.FLIP_LEFT_RIGHT, 3: Image.Transpose.ROTATE_180,
              4: Image.Transpose.FLIP_TOP_BOTTOM, 5: Image.Transpose.TRANSPOSE,
              6: Image.Transpose.ROTATE_270, 7: Image.Transpose.TRANSVERSE,
              8: Image.Transpose.ROTATE_90}


def quadrants(width=640, height=400, orientation=None):
    image=Image.new('RGB',(width,height));draw=ImageDraw.Draw(image)
    for box,color in [((0,0,width//2,height//2),'red'),((width//2,0,width,height//2),'green'),
                      ((0,height//2,width//2,height),'blue'),((width//2,height//2,width,height),'yellow')]:
        draw.rectangle(box,fill=color)
    stream=io.BytesIO()
    if orientation is None:image.save(stream,format='JPEG',quality=95)
    else:
        exif=Image.Exif();exif[274]=orientation;image.save(stream,format='JPEG',quality=95,exif=exif)
    return stream.getvalue()


def pixels(result):
    image=Image.open(io.BytesIO(base64.b64decode(result['thumbnail'])));image.load();return image


def corner_values(image):
    w,h=image.size
    return [image.getpixel((int(w*x),int(h*y))) for x,y in ((.2,.2),(.8,.2),(.2,.8),(.8,.8))]


class RawPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='lightharbor-raw-033-');self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def assert_transformed(self, result, original, orientation):
        with Image.open(io.BytesIO(original)) as expected:
            if orientation in TRANSFORMS:expected=expected.transpose(TRANSFORMS[orientation])
            expected.thumbnail((480,480))
            actual=pixels(result);self.assertEqual(actual.size,expected.size)
            self.assertNotIn(274,actual.getexif())
            for a,b in zip(corner_values(actual),corner_values(expected)):
                self.assertLess(max(abs(x-y) for x,y in zip(a,b)),16)
            self.assertEqual(actual.size,(result['width'],result['height']))
    def test_all_eight_raw_parent_orientation_transforms(self):
        jpeg=quadrants()
        for orientation in range(1,9):
            with self.subTest(orientation=orientation):
                file=self.root/f'generated-{orientation}.cr2';generated_embedded_cr2(file,orientation=orientation,jpeg=jpeg)
                before=file.read_bytes();result=media.thumbnail(file,raw=True)
                self.assert_transformed(result,jpeg,orientation);self.assertEqual(file.read_bytes(),before)
    def test_embedded_explicit_orientation_wins_and_is_not_rotated_twice(self):
        for own in (1,3,6,8):
            with self.subTest(own=own):
                jpeg=quadrants(orientation=own);file=self.root/f'own-{own}.cr2'
                generated_embedded_cr2(file,orientation=6,jpeg=jpeg)
                self.assert_transformed(media.thumbnail(file,raw=True),jpeg,own)
    def test_thumbnail_ifd_orientation_wins_over_raw_parent(self):
        jpeg=quadrants();file=self.root/'thumbnail-normal.cr2'
        generated_embedded_cr2(file,orientation=6,thumbnail_orientation=1,jpeg=jpeg)
        self.assert_transformed(media.thumbnail(file,raw=True),jpeg,1)
    def test_plain_jpeg_all_eight_transforms_and_no_upscale(self):
        for orientation in range(1,9):
            with self.subTest(orientation=orientation):
                data=quadrants(orientation=orientation);file=self.root/f'{orientation}.jpg';file.write_bytes(data)
                self.assert_transformed(media.thumbnail(file),data,orientation)
                high=media.thumbnail(file,'high');self.assertEqual(sorted((high['width'],high['height'])),[400,640])
    def test_tiff_loader_rotation_is_not_applied_twice(self):
        for orientation in range(1,9):
            with self.subTest(orientation=orientation):
                jpeg=quadrants();file=self.root/f'auto-{orientation}.tiff';exif=Image.Exif();exif[274]=orientation
                with Image.open(io.BytesIO(jpeg)) as image:image.save(file,exif=exif)
                self.assert_transformed(media.thumbnail(file),jpeg,orientation)
    def test_high_small_embedded_attempts_larger_raw_result(self):
        file=self.root/'small.cr2';generated_embedded_cr2(file,orientation=6,jpeg=quadrants(160,100))
        with Image.open(io.BytesIO(quadrants(2400,3600))) as image:
            alternative=media._encode(image,'high','RAW 解码预览')
        with patch.object(media,'_raw_preview',return_value=alternative) as decoder:
            result=media.thumbnail(file,'high',raw=True)
            decoder.assert_called_once();self.assertEqual(result['source'],'RAW 解码预览');self.assertEqual(result['height'],3200)
    def test_large_embedded_avoids_unnecessary_decode(self):
        file=self.root/'large.cr2';generated_embedded_cr2(file,orientation=6,jpeg=quadrants(3600,2400))
        with patch.object(media,'_raw_preview') as decoder:
            result=media.thumbnail(file,'high',raw=True);decoder.assert_not_called()
            self.assertEqual((result['width'],result['height']),(2133,3200));self.assertEqual(result['source'],'RAW 内嵌预览')
    def test_decode_failure_or_timeout_preserves_oriented_small_image(self):
        jpeg=quadrants(160,100);file=self.root/'small.cr2';generated_embedded_cr2(file,orientation=6,jpeg=jpeg)
        for warning,expected in (('此 RAW 暂时无法解码预览','解码未成功'),('RAW 预览读取超时','解码超时')):
            with self.subTest(warning=warning),patch.object(media,'_raw_preview',return_value={'thumbnail':None,'warning':warning}):
                result=media.thumbnail(file,'high',raw=True);self.assertEqual((result['width'],result['height']),(100,160))
                self.assertIn(expected,result['warning']);self.assertIn('保留现有预览',result['warning']);self.assertEqual(result['source'],'RAW 内嵌预览')
        with patch('windows_media.subprocess.run',side_effect=subprocess.TimeoutExpired('decoder',25)):
            self.assertIn('超时',media._raw_preview(file,'high')['warning'])
    def test_do_not_replace_larger_exif_preview_with_smaller_helper(self):
        file=self.root/'larger.cr2';generated_embedded_cr2(file,jpeg=quadrants(1000,600))
        with Image.open(io.BytesIO(quadrants(200,120))) as image:small=media._encode(image,'high','RAW 解码预览')
        with patch.object(media,'_raw_preview',return_value=small):
            result=media.thumbnail(file,'high',raw=True);self.assertEqual(result['width'],1000)
            self.assertIn('未放大',result['warning']);self.assertEqual(result['source'],'RAW 内嵌预览')
    def test_native_dng_low_high_correct_orientation_and_full_size(self):
        file=self.root/'generated.dng';generated_dng(file,width=960,height=640,orientation=6,embedded=quadrants(160,100))
        before=file.read_bytes();low=media.thumbnail(file,'low',True);high=media.thumbnail(file,'high',True)
        self.assertEqual(low['source'],'RAW 内嵌预览');self.assertEqual((low['width'],low['height']),(100,160))
        self.assertEqual(high['source'],'RAW 解码预览');self.assertEqual((high['width'],high['height']),(640,960))
        self.assertEqual(pixels(high).size,(640,960));self.assertEqual(file.read_bytes(),before)
    def test_native_dng_decode_all_rotations_mirrors_exactly_once(self):
        for orientation in range(1,9):
            with self.subTest(orientation=orientation):
                file=self.root/f'rotate-{orientation}.dng';generated_dng(file,orientation=orientation)
                result=media.thumbnail(file,'high',True);image=pixels(result)
                # Four generated grayscale quadrants have fixed intensity order.
                original=Image.new('L',(128,96));draw=ImageDraw.Draw(original)
                for box,value in [((0,0,64,48),0),((64,0,128,48),1),((0,48,64,96),2),((64,48,128,96),3)]:draw.rectangle(box,fill=value)
                if orientation in TRANSFORMS:original=original.transpose(TRANSFORMS[orientation])
                order=corner_values(original);actual=[sum(p) for p in corner_values(image)]
                self.assertEqual(sorted(range(4),key=lambda i:actual[i]),sorted(range(4),key=lambda i:order[i]))
                self.assertEqual(image.size,original.size)
    def test_native_large_dng_reaches_high_preview_without_enlarging(self):
        file=self.root/'large-generated.dng';generated_dng(file,width=3600,height=2400,orientation=8,embedded=quadrants(160,100))
        before=hashlib.sha256(file.read_bytes()).hexdigest();result=media.thumbnail(file,'high',True)
        self.assertEqual(result['source'],'RAW 解码预览');self.assertEqual((result['width'],result['height']),(2133,3200))
        self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(),before)
    def test_libraw_bitmap_preview_and_flip_without_exif(self):
        import rawpy
        import numpy as np
        data=quadrants()
        with Image.open(io.BytesIO(data)) as image:array=np.array(image)
        fake=Mock();fake.__enter__=Mock(return_value=fake);fake.__exit__=Mock(return_value=False)
        fake.sizes=SimpleNamespace(flip=6)
        fake.extract_thumb.return_value=SimpleNamespace(format=rawpy.ThumbFormat.BITMAP,data=array)
        with patch('rawpy.imread',return_value=fake),patch.object(media,'tags_for',return_value={}):
            result=media._decode_raw('generated.arw','low')
        self.assert_transformed(result,data,6);fake.postprocess.assert_not_called()
    def test_larger_libraw_jpeg_does_not_inherit_different_small_thumbnail_tag(self):
        import rawpy
        jpeg=quadrants(3600,2400)
        fake=Mock();fake.__enter__=Mock(return_value=fake);fake.__exit__=Mock(return_value=False)
        fake.sizes=SimpleNamespace(flip=6)
        fake.extract_thumb.return_value=SimpleNamespace(format=rawpy.ThumbFormat.JPEG,data=jpeg)
        tags={'Image Orientation':6,'Thumbnail Orientation':1,'JPEGThumbnail':quadrants(160,100)}
        with patch('rawpy.imread',return_value=fake),patch.object(media,'tags_for',return_value=tags):
            result=media._decode_raw('generated.nef','high')
        self.assertEqual((result['width'],result['height']),(2133,3200));fake.postprocess.assert_not_called()
    def test_decode_failure_keeps_libraw_larger_embedded_preview(self):
        import rawpy
        fake=Mock();fake.__enter__=Mock(return_value=fake);fake.__exit__=Mock(return_value=False)
        fake.sizes=SimpleNamespace(flip=6)
        fake.extract_thumb.return_value=SimpleNamespace(format=rawpy.ThumbFormat.JPEG,data=quadrants(1800,1200))
        fake.postprocess.side_effect=rawpy.LibRawDataError('generated failure')
        with patch('rawpy.imread',return_value=fake),patch.object(media,'tags_for',return_value={'Image Orientation':6}):
            result=media._decode_raw('generated.cr2','high')
        self.assertEqual((result['width'],result['height']),(1200,1800));self.assertIn('解码未成功',result['warning'])
        self.assertFalse(fake.postprocess.call_args.kwargs['half_size']);self.assertEqual(fake.postprocess.call_args.kwargs['user_flip'],0)


if __name__=='__main__':unittest.main()
