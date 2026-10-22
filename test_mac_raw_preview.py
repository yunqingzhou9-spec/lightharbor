"""Native ImageIO/Core Image regressions, only generated disposable fixtures."""
import base64,io,json,os,struct,subprocess,tempfile,unittest,hashlib
from array import array
from pathlib import Path
from PIL import Image,ImageDraw
S=lambda *v:struct.pack('<'+'H'*len(v),*v)
L=lambda *v:struct.pack('<'+'I'*len(v),*v)
R=lambda *v:b''.join(struct.pack('<ii',n,d) for n,d in v)

def generated_mac_dng(path, width=960, height=640, orientation=1, thumb_edge=None, valid_raw=True):
    w,h=width,height
    tw=min(160,w) if thumb_edge is None else thumb_edge;th=max(1,round(tw*h/w))
    thumb_pixels=bytes(c for y in range(th) for x in range(tw) for c in [50+(x>=tw//2)*60+(y>=th//2)*100]*3)
    global_tags={50706:(1,4,bytes([1,4,0,0])),50707:(1,4,bytes([1,1,0,0])),50708:(2,14,b'Generated DNG\0'),50721:(10,9,R((1,1),(0,1),(0,1),(0,1),(1,1),(0,1),(0,1),(0,1),(1,1))),50728:(5,3,R((1,1),(1,1),(1,1))),50778:(3,1,S(21)),271:(2,12,b'LightHarbor\0\0'),272:(2,14,b'Generated DNG\0'),274:(3,1,S(orientation))}
    thumb={254:(4,1,L(1)),256:(4,1,L(tw)),257:(4,1,L(th)),258:(3,3,S(8,8,8)),259:(3,1,S(1)),262:(3,1,S(2)),273:(4,1,L(0)),277:(3,1,S(3)),278:(4,1,L(th)),279:(4,1,L(tw*th*3)),284:(3,1,S(1)),330:(4,1,L(0)),**global_tags}
    raw={254:(4,1,L(0)),256:(4,1,L(w)),257:(4,1,L(h)),258:(3,1,S(16)),259:(3,1,S(1)),262:(3,1,S(32803)),273:(4,1,L(0)),277:(3,1,S(1)),278:(4,1,L(h)),279:(4,1,L(w*h*2)),284:(3,1,S(1)),33421:(3,2,S(2,2)),33422:(1,4,bytes([0,1,1,2])),50710:(1,3,bytes([0,1,2])),50711:(3,1,S(1)),50713:(3,2,S(1,1)),50714:(3,1,S(0)),50717:(4,1,L(4095)),50718:(5,2,R((1,1),(1,1))),50719:(4,2,L(0,0)),50720:(4,2,L(w,h)),50829:(4,4,L(0,0,h,w))}
    if not valid_raw:raw[262]=(3,1,S(0))
    raw_at=8+2+len(thumb)*12+4;extra_at=raw_at+2+len(raw)*12+4
    extra=bytearray()
    def layout(tags,rawflag=False):
     rows=[]
     for tag,(typ,count,data) in sorted(tags.items()):
      if len(data)>4:
       value=L(extra_at+len(extra));extra.extend(data)
       if len(extra)%2:extra.append(0)
      else:value=data.ljust(4,b'\0')
      rows.append((tag,typ,count,value))
     return rows
    a=layout(thumb);b=layout(raw);thumb_at=extra_at+len(extra);pixels_at=thumb_at+tw*th*3
    def encode(rows,rawflag=False):
     return S(len(rows))+b''.join(struct.pack('<HHI',tag,typ,count)+(L(pixels_at if rawflag else thumb_at) if tag==273 else L(raw_at) if tag==330 else value) for tag,typ,count,value in rows)+L(0)
    pixels=array('H',(400+(x>=w//2)*900+(y>=h//2)*1500 for y in range(h) for x in range(w)))
    Path(path).write_bytes(b'II*\0'+L(8)+encode(a)+encode(b,True)+extra+thumb_pixels+pixels.tobytes())


TRANSFORMS={2:Image.Transpose.FLIP_LEFT_RIGHT,3:Image.Transpose.ROTATE_180,4:Image.Transpose.FLIP_TOP_BOTTOM,5:Image.Transpose.TRANSPOSE,6:Image.Transpose.ROTATE_270,7:Image.Transpose.TRANSVERSE,8:Image.Transpose.ROTATE_90}
def corners(image):
 w,h=image.size
 return [image.getpixel((int(w*x),int(h*y))) for x,y in ((.2,.2),(.8,.2),(.2,.8),(.8,.8))]
class MacRawPreviewTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.helper=Path(os.environ['MEDIA_PROBE_TEST_BIN']);assert cls.helper.is_file()
 def setUp(self):self.temp=tempfile.TemporaryDirectory(prefix='lightharbor-mac-media-');self.root=Path(self.temp.name)
 def tearDown(self):self.temp.cleanup()
 def preview(self,path,quality='low',raw=True):
  before=hashlib.sha256(path.read_bytes()).hexdigest();result=subprocess.run([str(self.helper)],input=json.dumps({'path':str(path),'thumbnail':True,'quality':quality,'raw':raw})+'\n',text=True,capture_output=True,timeout=25,check=True)
  self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)
  value=json.loads(result.stdout);return value
 def image(self,result):
  self.assertTrue(result.get('thumbnail'),result);image=Image.open(io.BytesIO(base64.b64decode(result['thumbnail'])));image.load();self.assertEqual(image.size,(result['width'],result['height']));self.assertNotIn(274,image.getexif());return image
 def expected_order(self,w,h,orientation):
  im=Image.new('L',(w,h));draw=ImageDraw.Draw(im)
  for box,value in [((0,0,w//2,h//2),0),((w//2,0,w,h//2),1),((0,h//2,w//2,h),2),((w//2,h//2,w,h),3)]:draw.rectangle(box,fill=value)
  return im.transpose(TRANSFORMS[orientation]) if orientation in TRANSFORMS else im
 def check_order(self,image,orientation):
  expected=self.expected_order(160,round(160*640/960),orientation);a=[sum(p) for p in corners(image)];b=corners(expected)
  self.assertEqual(sorted(range(4),key=lambda i:a[i]),sorted(range(4),key=lambda i:b[i]));self.assertEqual(image.width<image.height,orientation in (5,6,7,8))
 def test_embedded_raw_all_eight_orientations(self):
  for orientation in range(1,9):
   with self.subTest(orientation=orientation):
    path=self.root/f'generated-{orientation}.dng';generated_mac_dng(path,orientation=orientation);result=self.preview(path);image=self.image(result);self.assertEqual(result['source'],'RAW 内嵌预览');self.check_order(image,orientation)
 def test_decoded_raw_all_eight_orientations_once(self):
  for orientation in range(1,9):
   with self.subTest(orientation=orientation):
    path=self.root/f'decode-{orientation}.dng';generated_mac_dng(path,orientation=orientation);result=self.preview(path,'high');image=self.image(result);self.assertEqual(result['source'],'RAW 解码预览');self.check_order(image,orientation);self.assertEqual(sorted(image.size),[640,960])
 def test_native_large_raw_high_real_pixels(self):
  path=self.root/'large.dng';generated_mac_dng(path,width=3600,height=2400,orientation=6);low=self.preview(path);high=self.preview(path,'high');self.assertEqual(low['source'],'RAW 内嵌预览');self.assertEqual(high['source'],'RAW 解码预览');self.assertEqual(high['height'],3200);self.assertIn(high['width'],(2133,2134));self.image(high)
 def test_large_embedded_does_not_need_raw_decode(self):
  path=self.root/'large-embedded.dng';generated_mac_dng(path,width=3600,height=2400,orientation=8,thumb_edge=3300);result=self.preview(path,'high');self.assertEqual(result['source'],'RAW 内嵌预览');self.assertEqual((result['width'],result['height']),(2133,3200));self.image(result)
 def test_unsupported_raw_reports_no_fabricated_preview(self):
  path=self.root/'unsupported-generated.dng';generated_mac_dng(path,orientation=6,valid_raw=False);result=self.preview(path,'high');self.assertFalse(result.get('thumbnail'));self.assertIn('无法解码',result['warning'])
 def test_available_embedded_preview_survives_unsupported_raw_decoder(self):
  # A generated JPEG with a real EXIF thumbnail, routed as RAW. ImageIO can
  # extract its thumbnail while CIRAWFilter cannot decode it as sensor data.
  chart=Image.new('RGB',(160,100));draw=ImageDraw.Draw(chart)
  for box,color in [((0,0,80,50),(50,50,50)),((80,0,160,50),(110,110,110)),((0,50,80,100),(150,150,150)),((80,50,160,100),(210,210,210))]:draw.rectangle(box,fill=color)
  out=io.BytesIO();chart.save(out,format='JPEG',quality=95);thumb=out.getvalue()
  main=io.BytesIO();Image.new('RGB',(640,400),'green').save(main,format='JPEG')
  ifd1=26;jpeg_at=ifd1+2+5*12+4
  tiff=b'II*\0'+L(8)+S(1)+struct.pack('<HHI',274,3,1)+S(6)+b'\0\0'+L(ifd1)+S(5)+struct.pack('<HHII',256,4,1,160)+struct.pack('<HHII',257,4,1,100)+struct.pack('<HHI',259,3,1)+S(6)+b'\0\0'+struct.pack('<HHII',513,4,1,jpeg_at)+struct.pack('<HHII',514,4,1,len(thumb))+L(0)+thumb
  app1=b'Exif\0\0'+tiff;path=self.root/'generated-embedded.cr2';path.write_bytes(b'\xff\xd8\xff\xe1'+struct.pack('>H',len(app1)+2)+app1+main.getvalue()[2:])
  result=self.preview(path,'high');image=self.image(result);self.assertEqual(result['source'],'RAW 内嵌预览');self.assertIn('保留现有预览',result['warning']);self.assertEqual(image.size,(100,160));self.check_order(image,6)
 def test_jpeg_and_tiff_rotation_mirror_no_double_transform(self):
  for ext in ('jpg','tiff'):
   for orientation in range(1,9):
    with self.subTest(ext=ext,orientation=orientation):
     im=self.expected_order(640,400,1).convert('RGB');draw=ImageDraw.Draw(im)
     for box,color in [((0,0,320,200),'red'),((320,0,640,200),'green'),((0,200,320,400),'blue'),((320,200,640,400),'yellow')]:draw.rectangle(box,fill=color)
     exif=Image.Exif();exif[274]=orientation;path=self.root/f'{orientation}.{ext}';im.save(path,exif=exif)
     expected=im.transpose(TRANSFORMS[orientation]) if orientation in TRANSFORMS else im;expected.thumbnail((480,480));actual=self.image(self.preview(path,raw=False));self.assertEqual(actual.size,expected.size)
     for a,b in zip(corners(actual),corners(expected)):self.assertLess(max(abs(x-y) for x,y in zip(a,b)),22)
 def test_small_jpeg_high_is_not_enlarged(self):
  path=self.root/'tiny.jpg';Image.new('RGB',(100,160),'green').save(path);result=self.preview(path,'high',False);self.assertEqual((result['width'],result['height']),(100,160));self.image(result)
 def test_corrupt_and_invalid_quality_report_failure(self):
  path=self.root/'corrupt.cr2';path.write_bytes(b'GENERATED CORRUPT RAW');self.assertFalse(self.preview(path).get('thumbnail'))
  path=self.root/'valid.dng';generated_mac_dng(path);self.assertIn('无效',self.preview(path,'original')['warning'])
if __name__=='__main__':unittest.main()
